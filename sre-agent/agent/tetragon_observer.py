"""Tetragon-event-to-NATS translator (Phase 21.2).

The security-domain sibling of `chaos_observer`. Tetragon (eBPF) enforces
TracingPolicies and, when configured with its Kubernetes event exporter,
surfaces policy matches as Kubernetes Events. This observer watches those
events, filters to the runtime-security signals the agent acts on, and
publishes them onto `security.events.<kind>` for the security NATS consumer.

Self-disabling by design: if the Tetragon CRD (`tracingpolicies.cilium.io`)
isn't present — the common case on a dev kind cluster, and ALWAYS the case
in CI — the observer logs one INFO line and returns without watching. The
synthetic generator (`agent.security_events`) is the dev/CI/fallback demo
path; this observer is the live-eBPF path for real workshop hardware.

Mirrors chaos_observer's machinery: debounce per (ns, kind, name, reason),
token-bucket rate limit, threaded K8s watch, loop-scheduled NATS publish.
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

# Map a Tetragon/security event `reason` → (subject suffix, priority).
# Tetragon's k8s-event exporter (and Falco-to-events sidecars) use reasons
# like these; anything not mapped is ignored.
SECURITY_REASON_MAP: dict[str, tuple[str, str]] = {
    "ProcessExec": ("suspicious_process", "Warning"),
    "TerminalShell": ("terminal_shell", "Critical"),
    "ReverseShell": ("terminal_shell", "Critical"),
    "SuspiciousProcess": ("suspicious_process", "Critical"),
    "CryptoMiner": ("suspicious_process", "Critical"),
    "PrivilegeEscalation": ("privilege_escalation", "Critical"),
    "SensitiveMount": ("privilege_escalation", "Critical"),
    "SuspiciousEgress": ("suspicious_egress", "Error"),
    "UnexpectedConnection": ("suspicious_egress", "Error"),
}

SYSTEM_NAMESPACES: frozenset[str] = frozenset(
    {"kube-system", "kube-public", "kube-node-lease", "cilium", "tetragon", "falco"}
)

DEBOUNCE_S = 60.0
RATE_LIMIT_BURST = 20
RATE_LIMIT_REFILL_PER_S = 1.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TetragonObserver:
    """Long-running asyncio task. `run()` returns the awaitable."""

    def __init__(self) -> None:
        self._last_emit: dict[tuple[str, str, str, str], float] = {}
        self._tokens = float(RATE_LIMIT_BURST)
        self._last_refill = time.monotonic()
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        self._stop.set()

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

    @staticmethod
    def _tetragon_present(api_ext) -> bool:
        """True if the Tetragon CRD is installed. Gate for self-disable."""
        try:
            api_ext.read_custom_resource_definition("tracingpolicies.cilium.io")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            import nats
            from kubernetes import client, config
        except ImportError as exc:
            log.warning("tetragon_observer disabled (missing dep: %s)", exc)
            return

        try:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config(config_file=settings.kubeconfig)
        except Exception as exc:  # noqa: BLE001
            log.info("tetragon_observer: no kube auth (%s) — disabled", exc)
            return

        # Self-disable cleanly when Tetragon isn't installed (dev/CI).
        if not self._tetragon_present(client.ApiextensionsV1Api()):
            log.info(
                "tetragon_observer: Tetragon CRD not found — disabled. "
                "Install with `keosctl deps install tetragon`, or use "
                "`python -m agent.security_events` for synthetic events."
            )
            return

        try:
            nc = await nats.connect(settings.nats_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("tetragon_observer: NATS unreachable (%s) — disabled", exc)
            return

        js = nc.jetstream()
        try:
            await js.add_stream(name="SECURITY", subjects=["security.events.>"])
        except Exception:  # noqa: BLE001
            pass

        api = client.CoreV1Api()
        log.info("tetragon_observer: watching K8s Events for runtime-security signals")
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._consume_one_stream, api, js)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("tetragon_observer stream error: %s — retry in 5s", exc)
                await asyncio.sleep(5)
        try:
            await nc.drain()
        except Exception:  # noqa: BLE001
            pass

    def _consume_one_stream(self, api, js) -> None:
        from kubernetes import watch

        w = watch.Watch()
        try:
            for ev in w.stream(api.list_event_for_all_namespaces, timeout_seconds=300):
                if self._stop.is_set():
                    w.stop()
                    return
                obj = ev.get("object")
                if obj is not None:
                    self._handle(obj, js)
        finally:
            try:
                w.stop()
            except Exception:  # noqa: BLE001
                pass

    def _handle(self, obj: Any, js) -> None:
        reason = getattr(obj, "reason", None)
        if reason not in SECURITY_REASON_MAP:
            return
        involved = getattr(obj, "involved_object", None)
        ns = getattr(involved, "namespace", None) or getattr(
            getattr(obj, "metadata", None), "namespace", None
        )
        if not ns or ns in SYSTEM_NAMESPACES:
            return
        name = getattr(involved, "name", "") or ""
        kind = getattr(involved, "kind", "") or "Pod"
        key = (ns, kind, name, reason)
        now = time.monotonic()
        if now - self._last_emit.get(key, 0.0) < DEBOUNCE_S:
            return
        if not self._take_token():
            return
        self._last_emit[key] = now

        suffix, priority = SECURITY_REASON_MAP[reason]
        event = {
            "uuid": getattr(getattr(obj, "metadata", None), "uid", "") or "",
            "time": _now_iso(),
            "rule": f"{reason} on {kind}/{name}",
            "priority": priority,
            "source": "tetragon",
            "domain": "security",
            "output": (getattr(obj, "message", "") or "")[:500],
            "output_fields": {
                "event.type": reason,
                "k8s.ns.name": ns,
                "k8s.pod.name": name if kind == "Pod" else "",
                "involved.kind": kind,
                "involved.name": name,
                "evt.time": _now_iso(),
            },
        }
        subject = f"security.events.{suffix}"
        # Schedule the publish back on the main loop (we're in a worker thread).
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._publish(js, subject, event), self._loop)

    async def _publish(self, js, subject: str, event: dict[str, Any]) -> None:
        try:
            await js.publish(subject, json.dumps(event).encode("utf-8"))
            log.info("tetragon_observer: published %s (%s)", subject, event["rule"])
        except Exception as exc:  # noqa: BLE001
            log.warning("tetragon_observer: publish failed (%s)", exc)


# Module-level singleton, mirroring chaos_observer.observer.
observer = TetragonObserver()
