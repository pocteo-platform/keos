"""Kubernetes-event-to-NATS translator.

The agent doesn't watch the cluster directly. Instead, this observer runs
as an asyncio task next to the API + NATS consumer (see ``agent/serve.py``)
and translates relevant K8s events into ``infra.events.<kind>`` NATS
messages that the existing graph consumer picks up.

Why this layer instead of a direct K8s watch in the graph:

  - The graph is event-driven; events arrive on NATS, the graph runs.
  - K8s emits MANY events; the observer is where we filter + debounce.
  - Multiple telemetry sources (Chaos Mesh CRs, kubelet events, future
    custom controllers) all converge here into the same shape.

Debounce: 60 s per (namespace, involved_kind, involved_name, reason).
Without it, a single CrashLoopBackOff pod would flood NATS with one event
every 10 s for the lifetime of the loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from agent.config import settings

log = logging.getLogger(__name__)

# Map K8s event `reason` → (NATS subject suffix, Falco-ish priority).
# Anything not in this map is ignored.
REASON_MAP: dict[str, tuple[str, str]] = {
    "OOMKilled": ("oom_killed", "Critical"),
    "BackOff": ("crashloop", "Error"),
    "Killed": ("killed", "Warning"),
    "FailedScheduling": ("failed_scheduling", "Error"),
    "FailedMount": ("failed_mount", "Error"),
    "Unhealthy": ("unhealthy", "Warning"),
    "ImagePullBackOff": ("image_pull_failure", "Warning"),
    "ErrImagePull": ("image_pull_failure", "Warning"),
    "Failed": ("image_pull_failure", "Warning"),  # often image pull
    "NodeNotReady": ("node_not_ready", "Critical"),
    "Evicted": ("node_pressure", "Error"),
    "NodeHasMemoryPressure": ("node_pressure", "Critical"),
    "NodeHasDiskPressure": ("node_pressure", "Critical"),
    "NodeHasPIDPressure": ("node_pressure", "Critical"),
}

# Namespaces we never watch — control-plane / infra-only. Even if K8s
# emits noisy events here we don't want the agent to act on them.
SYSTEM_NAMESPACES: frozenset[str] = frozenset(
    {
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "chaos-mesh",  # the chaos controller's own pods (would loop on its own restarts)
        "local-path-storage",  # k3d/k3s storage provisioner
    }
)


def _watched_namespaces() -> set[str] | None:
    """Compute the namespace set the observer should watch.

    If `WATCH_NAMESPACES` env var is set (comma-separated), use that as an
    explicit allowlist (e.g. `workloads,production,staging`). Otherwise
    return None, meaning "watch all namespaces and filter out the SYSTEM
    deny-list at delivery time". The None branch picks up new namespaces
    automatically as they're created — preferred default for autonomy.
    """
    import os

    raw = (os.environ.get("WATCH_NAMESPACES") or "").strip()
    if not raw:
        return None
    return {n.strip() for n in raw.split(",") if n.strip()}


# Debounce window — one publish per (ns, kind, name, reason) per N seconds.
DEBOUNCE_S = 60.0

# Rate cap on observer publishes so a misbehaving cluster cannot DoS the
# agent. Burst of 20, refilled at 1/sec.
RATE_LIMIT_BURST = 20
RATE_LIMIT_REFILL_PER_S = 1.0


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


class ChaosObserver:
    """Long-running asyncio task. `start()` returns the awaitable."""

    def __init__(self) -> None:
        self._last_emit: dict[tuple[str, str, str, str], float] = {}
        self._tokens = float(RATE_LIMIT_BURST)
        self._last_refill = time.monotonic()
        self._stop = asyncio.Event()
        # Captured in `run()` when we're on the main asyncio thread. The
        # worker-thread watch loop uses this to schedule NATS publishes
        # back onto the right event loop. `asyncio.get_event_loop()` from
        # a non-main thread raises on Python 3.10+, which is the bug this
        # field exists to dodge.
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        self._stop.set()

    # ---- rate limit ------------------------------------------------------

    def _take_token(self) -> bool:
        now = time.monotonic()
        self._tokens = min(
            RATE_LIMIT_BURST,
            self._tokens + (now - self._last_refill) * RATE_LIMIT_REFILL_PER_S,
        )
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    # ---- main loop -------------------------------------------------------

    async def run(self) -> None:
        """Connect to the cluster, watch Events, publish to NATS."""
        # Capture the running loop so worker threads can schedule publishes
        # back on it. Must happen here (we're on the main asyncio thread)
        # before any kubernetes.Watch().stream() call moves us into a worker.
        self._loop = asyncio.get_running_loop()

        try:
            import nats

            # `watch` is consumed via `from kubernetes import watch` again
            # inside `_consume_one_stream`. Imported here purely as an
            # import-availability probe so a missing dep fails fast at
            # startup instead of crashing the watch thread later.
            from kubernetes import client, config, watch  # noqa: F401
        except ImportError as exc:
            log.warning("chaos_observer disabled (missing dep: %s)", exc)
            return

        # Try in-cluster auth first (Pod with projected SA token),
        # fall back to the dev kubeconfig file. Without this fallback
        # the observer is silently dead on every real cluster install
        # and the agent never sees real kubelet events.
        try:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config(config_file=settings.kubeconfig)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "chaos_observer: in-cluster + kubeconfig both failed (%s) — disabled",
                exc,
            )
            return

        try:
            nc = await nats.connect(settings.nats_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("chaos_observer: NATS unreachable (%s) — disabled", exc)
            return

        js = nc.jetstream()
        try:
            await js.add_stream(name="INFRA", subjects=["infra.events.>"])
        except Exception:  # noqa: BLE001
            pass

        api = client.CoreV1Api()
        allow = _watched_namespaces()
        if allow:
            log.info("chaos_observer: allowlist mode — watching ns=%s", sorted(allow))
        else:
            log.info(
                "chaos_observer: deny-list mode — watching ALL namespaces except %s",
                sorted(SYSTEM_NAMESPACES),
            )
        # Stash on self so _handle() can apply the filter without re-parsing env.
        self._allow_ns = allow

        # The kubernetes-client watch is blocking; offload to a thread.
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._consume_one_stream, api, js)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("chaos_observer stream error: %s — retrying in 5 s", exc)
                await asyncio.sleep(5)

        try:
            await nc.drain()
        except Exception:  # noqa: BLE001
            pass

    def _consume_one_stream(self, api, js) -> None:
        """Single watch session. Returns when the watch times out so the
        outer loop reconnects. Runs in a thread.

        Uses `list_event_for_all_namespaces` instead of
        `list_namespaced_event` so we get cluster-wide events in one
        stream, then filter in `_handle`. This lets the agent discover
        new namespaces automatically — no restart needed when an operator
        creates `production` or `staging`."""
        from kubernetes import watch

        w = watch.Watch()
        try:
            for ev in w.stream(
                api.list_event_for_all_namespaces,
                timeout_seconds=300,
            ):
                if self._stop.is_set():
                    w.stop()
                    return
                obj = ev.get("object")
                if obj is None:
                    continue
                self._handle(obj, js)
        finally:
            w.stop()

    def _handle(self, k8s_event, js) -> None:
        """One K8s Event in → at most one NATS publish out."""
        reason = getattr(k8s_event, "reason", "") or ""
        if reason not in REASON_MAP:
            return

        involved = getattr(k8s_event, "involved_object", None)
        if involved is None:
            return
        kind = getattr(involved, "kind", "") or "Unknown"
        name = getattr(involved, "name", "") or ""
        ns = getattr(involved, "namespace", "") or ""

        # Namespace filter: explicit allowlist (if set) OR deny-list of
        # system namespaces. Node-scoped events (kind=Node) have empty
        # namespace — always pass through, those are cluster-level.
        if kind != "Node" and ns:
            allow = getattr(self, "_allow_ns", None)
            if allow is not None:
                if ns not in allow:
                    return
            else:
                if ns in SYSTEM_NAMESPACES:
                    return

        key = (ns, kind, name, reason)
        now = time.monotonic()
        last = self._last_emit.get(key, 0.0)
        if now - last < DEBOUNCE_S:
            return

        if not self._take_token():
            log.warning(
                "chaos_observer: rate-limited, dropping %s/%s reason=%s", kind, name, reason
            )
            try:
                from agent import metrics as _m

                _m.chaos_observer_errors_total.labels(kind="rate_limited").inc()
            except Exception:  # noqa: BLE001
                pass
            return

        self._last_emit[key] = now

        subject_suffix, priority = REASON_MAP[reason]
        subject = f"infra.events.{subject_suffix}"

        payload = self._build_event(k8s_event, reason, priority, kind, name, ns)

        # Schedule the publish on the main loop. We're in a worker thread
        # (kubernetes.Watch().stream() is blocking + offloaded via
        # asyncio.to_thread), so we must use the loop captured in `run()`.
        # Calling asyncio.get_event_loop() here would raise on Python 3.10+.
        loop = self._loop
        if loop is None:
            log.warning("chaos_observer: no captured loop, dropping publish")
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(
                js.publish(subject, json.dumps(payload).encode("utf-8")),
                loop,
            )
            # Block briefly so a failed publish surfaces as a log line on
            # this thread instead of silently dying. 3 s is well above the
            # NATS round-trip; if we hit the timeout something is genuinely
            # wrong with the bus.
            fut.result(timeout=3)
        except Exception as exc:  # noqa: BLE001
            log.warning("chaos_observer: publish failed: %s", exc)
            try:
                from agent import metrics as _m

                _m.chaos_observer_errors_total.labels(kind="publish_failed").inc()
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                from agent import metrics as _m

                _m.chaos_observer_events_total.labels(reason=reason).inc()
            except Exception:  # noqa: BLE001
                pass
            log.info("chaos_observer: published %s for %s/%s", subject, kind, name)

    def _build_event(
        self, k8s_event, reason: str, priority: str, kind: str, name: str, ns: str
    ) -> dict[str, Any]:
        message = getattr(k8s_event, "message", "") or ""
        return {
            "uuid": getattr(k8s_event.metadata, "uid", "") if k8s_event.metadata else "",
            "time": _now_iso(),
            "rule": f"{reason} on {kind}/{name}",
            "priority": priority,
            "source": "kubelet",
            "output": message[:500],
            "output_fields": {
                "event.kind": reason,
                "kubelet.reason": reason,
                "k8s.ns.name": ns,
                "k8s.pod.name": name if kind == "Pod" else "",
                "k8s.deployment.name": name if kind == "Deployment" else "",
                "k8s.node.name": name if kind == "Node" else "",
                "involved.kind": kind,
                "involved.name": name,
                "evt.time": _now_iso(),
            },
        }


# Module-level singleton — `agent/serve.py` imports + awaits this.
observer = ChaosObserver()
