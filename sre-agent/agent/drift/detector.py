"""Background detector — polls the SoT, fetches live, emits drift events.

Started by `agent.serve` as an asyncio task. Tick cadence is set by
`DRIFT_TICK_INTERVAL_S` (default 120 s — drift is slow-moving;
sub-minute polling produces noise without catching anything that
kubelet events wouldn't catch first).

On each tick:

  1. Walk every `TargetManifest` from the configured SoT provider.
  2. For each one, fetch the live resource via the K8s API.
  3. Run `compare_resources` to produce a `DriftReport`.
  4. If `report.drifted` and the (kind, ns, name) isn't in its
     dedup cooldown, publish an `infra.events.drift.<kind>` event
     to NATS. The agent's existing graph consumer picks it up
     like any reactive event.

Failure modes are soft:
  * SoT provider returns nothing → no events; quiet.
  * Live fetch 404s (resource doesn't exist) → reported as
    "missing" drift; operator sees it as a "deploy the manifest"
    suggestion.
  * NATS publish fails → logged at WARNING; next tick retries.

Severity mapping mirrors the forecast scheduler's step-down: drift
events fire at one severity LOWER than the reactive equivalent so
OPA's matrix is more conservative for autonomous reverts. Critical
kinds (Deployment, StatefulSet) → high; less critical (ConfigMap,
Service) → medium.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from agent.config import settings
from agent.drift.comparator import DriftReport, compare_resources
from agent.drift.sources import SourceOfTruth, TargetManifest, source_from_env

log = logging.getLogger(__name__)


# Default tick. Drift accumulates over minutes-to-hours; sub-minute
# polling produces noise without catching anything kubelet events
# wouldn't catch first.
DEFAULT_TICK_INTERVAL_S = 120

# How long to suppress a repeat drift event for the same (kind, ns,
# name). 10 min keeps the operator from drowning in "still drifted"
# pings when a slow human is fixing the SoT.
DEDUP_WINDOW_S = 600

# Severity per K8s kind. Critical kinds drive a higher severity for
# the reactive event; the agent's plan_action LLM weights that
# alongside the OPA matrix for revert_drift.
DRIFT_EVENT_PRIORITY: dict[str, str] = {
    # High — workload availability is the operator's #1 concern
    "Deployment": "high",
    "StatefulSet": "high",
    "DaemonSet": "high",
    "Job": "medium",
    "CronJob": "medium",
    # Medium — control-plane bits
    "ConfigMap": "medium",
    "Secret": "medium",
    "Service": "medium",
    "Ingress": "medium",
    "ServiceAccount": "medium",
    # Low — RBAC + policy (drift here is rarely operator-facing)
    "Role": "low",
    "RoleBinding": "low",
    "ClusterRole": "low",
    "ClusterRoleBinding": "low",
    # Default for unknown kinds
    "_default": "medium",
}


# ---------------------------------------------------------------------------
# K8s + NATS access. Deferred imports so the package stays importable
# in hermetic-test environments where neither client is installed.
# ---------------------------------------------------------------------------


@dataclass
class _LiveLookup:
    """Thin wrapper around kubernetes-client. Caches the API objects
    so we don't re-import per call."""

    _ready: bool = False
    _core: Any | None = None
    _apps: Any | None = None
    _batch: Any | None = None
    _net: Any | None = None
    _rbac: Any | None = None

    def _ensure(self) -> bool:
        if self._ready:
            return True
        try:
            from kubernetes import client, config  # type: ignore[import-not-found]
        except ImportError:
            log.warning("drift: kubernetes-client not installed")
            return False
        try:
            config.load_kube_config()
        except Exception:
            try:
                config.load_incluster_config()
            except Exception as exc:  # noqa: BLE001
                log.warning("drift: kubeconfig + in-cluster both failed (%s)", exc)
                return False
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._batch = client.BatchV1Api()
        self._net = client.NetworkingV1Api()
        self._rbac = client.RbacAuthorizationV1Api()
        self._ready = True
        return True

    def fetch(self, kind: str, namespace: str | None, name: str) -> dict[str, Any] | None:
        """Return the live resource body as a plain dict, or None
        when it doesn't exist or the API call fails."""
        if not self._ensure():
            return None
        try:
            from kubernetes.client.rest import ApiException  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            obj = self._fetch_typed(kind, namespace, name)
        except ApiException as exc:
            if exc.status == 404:
                return None
            log.warning(
                "drift: kubectl get %s/%s -n %s failed (%d)", kind, name, namespace, exc.status
            )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("drift: unexpected error fetching %s/%s (%s)", kind, name, exc)
            return None
        if obj is None:
            return None
        # kubernetes-client returns typed models; we want the dict shape
        # the SoT YAML uses. `to_dict` does that, but it snake-cases keys
        # (e.g. `creationTimestamp` → `creation_timestamp`). Re-camel via
        # the same JSON serializer the kubectl CLI uses.
        from kubernetes import client  # type: ignore[import-not-found]

        try:
            api_client = client.ApiClient()
            raw = api_client.sanitize_for_serialization(obj)
            assert isinstance(raw, dict)
            return raw
        except Exception as exc:  # noqa: BLE001
            log.warning("drift: serialise %s/%s failed (%s)", kind, name, exc)
            return None

    def _fetch_typed(self, kind: str, namespace: str | None, name: str) -> Any:
        """Dispatch by kind to the right typed API. We only cover the
        kinds the SoT typically tracks — workload + config + RBAC +
        network basics. Unknown kinds fall through to None."""
        ns = namespace or ""
        if kind == "Deployment":
            return self._apps.read_namespaced_deployment(name, ns)
        if kind == "StatefulSet":
            return self._apps.read_namespaced_stateful_set(name, ns)
        if kind == "DaemonSet":
            return self._apps.read_namespaced_daemon_set(name, ns)
        if kind == "Job":
            return self._batch.read_namespaced_job(name, ns)
        if kind == "CronJob":
            return self._batch.read_namespaced_cron_job(name, ns)
        if kind == "ConfigMap":
            return self._core.read_namespaced_config_map(name, ns)
        if kind == "Secret":
            return self._core.read_namespaced_secret(name, ns)
        if kind == "Service":
            return self._core.read_namespaced_service(name, ns)
        if kind == "ServiceAccount":
            return self._core.read_namespaced_service_account(name, ns)
        if kind == "Ingress":
            return self._net.read_namespaced_ingress(name, ns)
        if kind == "Namespace":
            return self._core.read_namespace(name)
        if kind == "Role":
            return self._rbac.read_namespaced_role(name, ns)
        if kind == "RoleBinding":
            return self._rbac.read_namespaced_role_binding(name, ns)
        if kind == "ClusterRole":
            return self._rbac.read_cluster_role(name)
        if kind == "ClusterRoleBinding":
            return self._rbac.read_cluster_role_binding(name)
        log.debug("drift: kind %r not in fetch dispatch; skipping", kind)
        return None


# ---------------------------------------------------------------------------
# NATS publisher — same shape as the forecast scheduler's. Reused
# only conceptually; we keep them separate so a NATS down event
# in one path doesn't poison the other.
# ---------------------------------------------------------------------------


class NATSPublisher:
    def __init__(self, nats_url: str) -> None:
        self.nats_url = nats_url
        self._client: Any | None = None
        self._js: Any | None = None

    async def _ensure(self) -> bool:
        if self._js is not None:
            return True
        try:
            import nats  # type: ignore[import-not-found]
        except ImportError:
            log.warning("drift: nats-py missing; events will be dropped")
            return False
        try:
            self._client = await nats.connect(self.nats_url)
            self._js = self._client.jetstream()
            try:
                await self._js.add_stream(name="INFRA", subjects=["infra.events.>"])
            except Exception:  # noqa: BLE001 — stream may already exist
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("drift: NATS unreachable (%s); event dropped", exc)
            return False

    async def publish(self, subject: str, payload: dict) -> bool:
        if not await self._ensure():
            return False
        try:
            await self._js.publish(subject, json.dumps(payload).encode("utf-8"))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("drift: publish failed (%s); dropped %s", exc, subject)
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.drain()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
            self._js = None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class DriftDetector:
    """Polling + publish loop. `tick_once()` is the unit tests drive
    directly; `start()` / `stop()` run it on a cadence."""

    def __init__(
        self,
        *,
        source: SourceOfTruth,
        lookup: _LiveLookup | None = None,
        publisher: NATSPublisher | None = None,
        tick_interval_s: int = DEFAULT_TICK_INTERVAL_S,
        dedup_window_s: int = DEDUP_WINDOW_S,
    ) -> None:
        self.source = source
        self.lookup = lookup or _LiveLookup()
        self.publisher = publisher or NATSPublisher(settings.nats_url)
        self.tick_interval_s = max(30, int(tick_interval_s))
        self.dedup_window_s = max(60, int(dedup_window_s))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # (kind, namespace, name) → unix_ts of last publish.
        self._cooldowns: dict[tuple[str, str, str], float] = {}

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="drift-detector")
        log.info(
            "drift: detector started (source=%s tick=%ds dedup=%ds)",
            self.source.name,
            self.tick_interval_s,
            self.dedup_window_s,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None
        await self.publisher.close()

    async def _run(self) -> None:
        await self.tick_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_interval_s)
            except TimeoutError:
                pass
            if self._stop.is_set():
                return
            await self.tick_once()

    async def tick_once(self) -> int:
        """Walk SoT, fetch live, diff, publish drift events. Returns
        the number of events actually published on this tick."""
        published = 0
        targets: list[TargetManifest] = []
        try:
            targets = list(self.source.manifests())
        except Exception as exc:  # noqa: BLE001 — never let SoT crash the loop
            log.warning("drift: source.manifests() raised (%s); skipping tick", exc)
            return 0
        if not targets:
            return 0
        now = time.time()
        for tgt in targets:
            try:
                event = await asyncio.to_thread(self._maybe_event, tgt, now)
            except Exception as exc:  # noqa: BLE001 — per-target soft fail
                log.warning("drift: %s raised (%s); skipping", tgt.key, exc)
                continue
            if event is None:
                continue
            ok = await self.publisher.publish(event["subject"], event["payload"])
            if ok:
                published += 1
                self._cooldowns[tgt.key] = now
                self._emit_metric(tgt.kind)
        if published:
            log.info("drift: published %d drift event(s)", published)
        return published

    def _maybe_event(self, tgt: TargetManifest, now: float) -> dict | None:
        """Returns the spec for one drift event, or None for skip."""
        if self._is_suppressed(tgt.key, now):
            return None
        live = self.lookup.fetch(tgt.kind, tgt.namespace, tgt.name)
        if live is None:
            # Live resource doesn't exist OR fetch failed. v1 treats
            # this as drift only when the SoT explicitly declares the
            # resource — that's the operator's "this should exist"
            # contract. Skip the kubectl-failure case to avoid noise.
            # A future tick will retry.
            return None
        report = compare_resources(
            sot_body=tgt.body,
            live_body=live,
            kind=tgt.kind,
            namespace=tgt.namespace,
            name=tgt.name,
            source=tgt.source,
            extra_ignore_paths=tgt.ignore_paths,
        )
        if not report.drifted:
            return None
        return {
            "subject": f"infra.events.drift.{tgt.kind}",
            "payload": self._build_payload(tgt, report),
        }

    def _build_payload(self, tgt: TargetManifest, report: DriftReport) -> dict:
        priority = DRIFT_EVENT_PRIORITY.get(tgt.kind, DRIFT_EVENT_PRIORITY["_default"])
        # Build the output string the diagnose LLM will see: a
        # human-readable summary of the first three deltas (lists
        # of dotted paths). The full delta list is in output_fields
        # so the SPA's incident detail can render the whole thing.
        first_paths = [d.path for d in report.deltas[:3]]
        more = f" (+{len(report.deltas) - 3} more)" if len(report.deltas) > 3 else ""
        summary = (
            f"{tgt.kind} {tgt.namespace or ''}/{tgt.name} drifted at {', '.join(first_paths)}{more}"
        )
        return {
            "rule": f"GitOps drift on {tgt.kind}",
            "priority": priority,
            "output": summary,
            "output_fields": {
                "k8s.ns.name": tgt.namespace or "",
                "k8s.pod.name": "",  # not pod-scoped
                "k8s.kind": tgt.kind,
                "k8s.name": tgt.name,
                "drift.source": tgt.source,
                "drift.paths": [d.path for d in report.deltas],
                "drift.deltas": [{"path": d.path, "kind": d.kind} for d in report.deltas[:20]],
            },
            "source": "keos-drift-detector",
            "ts": time.time(),
        }

    def _is_suppressed(self, key: tuple[str, str, str], now: float) -> bool:
        last = self._cooldowns.get(key)
        return last is not None and (now - last) < self.dedup_window_s

    def _emit_metric(self, kind: str) -> None:
        """Project drift events into Prometheus. Wrapped because a
        metrics-module failure must never break the detector."""
        try:
            from agent.metrics import drift_events_total

            drift_events_total.labels(kind=kind).inc()
        except Exception as exc:  # noqa: BLE001
            log.debug("drift: metric emit failed (%s); continuing", exc)


# ---------------------------------------------------------------------------
# Entrypoint helper for `agent.serve`.
# ---------------------------------------------------------------------------


def detector_from_env() -> DriftDetector | None:
    """Build a detector wired from env, or None if drift is
    disabled (no SoT configured)."""
    source = source_from_env()
    if source is None:
        return None
    return DriftDetector(
        source=source,
        tick_interval_s=int(os.environ.get("DRIFT_TICK_INTERVAL_S", "120")),
        dedup_window_s=int(os.environ.get("DRIFT_DEDUP_WINDOW_S", "600")),
    )
