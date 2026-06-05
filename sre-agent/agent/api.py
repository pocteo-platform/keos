"""FastAPI app exposing 16 REST endpoints + /ws/events WebSocket.

Embedded in the agent's serve process; uvicorn runs in the same asyncio
loop as the NATS subscriber + ModeStore watcher. The TraceBus is shared
in-memory so WebSocket subscribers see pipeline events with zero
serialisation cost.

Action endpoints (`/api/actions/approve`, `/api/scenarios/run`) ALL route
through the MCP server, never directly to agent.tools. Same OPA gate as
the agent's own execution path.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from limits import parse as _parse_rate_limit
from limits.storage import MemoryStorage as _RateLimitStorage
from limits.strategies import MovingWindowRateLimiter as _RateLimitStrategy

from agent import (
    approvals,
    incident_store,
    pipeline_trace,
    synthetic_events,
    usecase_runner,
    usecase_store,
)
from agent.api_auth import check_ws_token, get_token, require_bearer
from agent.api_models import (
    ActiveIncident,
    AgentSelf,
    ApprovalRequest,
    ApprovalResponse,
    AuditList,
    AuditRecord,
    AuditVerifyResponse,
    BaselineList,
    BaselineSnapshotModel,
    CalibrationList,
    CalibrationRule,
    CalibrationThresholdRequest,
    ClusterContext,
    DenialRequest,
    DeploymentSummary,
    DetectedOperators,
    EventSummary,
    ForecastList,
    ForecastRow,
    HealthResponse,
    IncidentDetail,
    IncidentList,
    IncidentSummary,
    ModeChangeRequest,
    ModeResponse,
    NamespaceSummary,
    NodeContext,
    NodeSummary,
    PendingApproval,
    PodSummary,
    RetentionDays,
    RetentionDiskUsage,
    RetentionState,
    RetentionUpdateRequest,
    ScenarioDescriptor,
    ScenarioRunRequest,
    ScenarioRunResponse,
    SceneOutcome,
    SceneResource,
    SceneStep,
    UseCase,
    UseCaseRunResponse,
    WorkloadScene,
)
from agent.audit import AuditLogger, verify_audit_log
from agent.config import settings
from agent.mode_store import mode_store
from agent.pipeline_trace import bus as trace_bus
from agent.ws_bridge import WSConnection

log = logging.getLogger(__name__)

# Module-level audit logger singleton. Writes go to settings.audit_log_path;
# the HMAC chain is per-line and idempotent, so creating a second logger
# here (in addition to the one in agent/graph.py) is safe — they write to
# the same file from different code paths.
_audit = AuditLogger()


app = FastAPI(
    title=os.environ.get("POCTEO_PRODUCT_NAME", "Keos SRE Agent") + " API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


# ---------------------------------------------------------------------------
# CORS allowed origins.
#
# KEOS_ALLOWED_ORIGINS env var = comma-separated list. Used by the Helm chart
# to lock down to the production Ingress host. When unset, the dev defaults
# are used so `npm run dev` against :5173 still works.
# ---------------------------------------------------------------------------
_DEV_CORS_DEFAULTS = [
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
]


def _cors_allowed_origins() -> list[str]:
    raw = (os.environ.get("KEOS_ALLOWED_ORIGINS") or "").strip()
    if not raw:
        return _DEV_CORS_DEFAULTS
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Per-IP rate limiting.
#
# One global ceiling keyed by client IP, applied to every route EXCEPT
# Kubernetes probe paths and the Prometheus scrape target. Backed by the
# `limits` library (the same engine slowapi uses); we skip slowapi's
# request-decoration layer because every route would otherwise need a
# decorator. A small custom middleware is clearer than a 30-route sprinkle.
#
# Tune via env without rebuilding:
#   KEOS_RATE_LIMIT_GLOBAL   e.g. "120/minute" (default)
#
# Defaults: 120/min/IP. Normal SPA usage is ~30/min (one /api/incidents
# poll every 2 s during pipeline replay). Anything well above that is
# either a scraper, a tight retry loop, or a real burst — all worth
# slowing down at the front door.
# ---------------------------------------------------------------------------
_RATE_GLOBAL = os.environ.get("KEOS_RATE_LIMIT_GLOBAL", "120/minute")
# Probe + scrape paths are exempt entirely — a 429 on a liveness probe
# would cascade into kubelet restart loops; a 429 on /metrics would silence
# Prometheus during incidents. Both worse than rate-limit bypass.
_RATE_LIMIT_EXEMPT_PATHS = frozenset(
    {
        "/api/health",
        "/api/health/detailed",
        "/metrics",
        "/api/metrics",
    }
)

_RATE_LIMIT_ITEM = _parse_rate_limit(_RATE_GLOBAL)
_RATE_LIMIT_STORAGE = _RateLimitStorage()
_RATE_LIMITER = _RateLimitStrategy(_RATE_LIMIT_STORAGE)


def _client_ip(request: Request) -> str:
    """Trust X-Forwarded-For when present (we sit behind Caddy / Ingress),
    fall back to the direct socket peer."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def _global_rate_limit(request: Request, call_next):
    """Enforce the global rate limit, skipping probe + scrape paths."""
    if request.url.path in _RATE_LIMIT_EXEMPT_PATHS:
        return await call_next(request)
    key = _client_ip(request)
    try:
        allowed = _RATE_LIMITER.hit(_RATE_LIMIT_ITEM, "global", key)
    except Exception as exc:  # noqa: BLE001 — never let rate-limit failures break the API
        log.warning("rate-limit check failed (%s); allowing request", exc)
        return await call_next(request)
    # Logged at WARNING only when blocked, to keep normal traffic quiet.
    if not allowed:
        log.warning("rate-limit %s blocked path=%s key=%s", _RATE_GLOBAL, request.url.path, key)
    if not allowed:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=429,
            content={
                "detail": f"rate limit exceeded ({_RATE_GLOBAL} per IP)",
                "limit": str(_RATE_LIMIT_ITEM),
            },
            headers={"Retry-After": "30"},
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Health (open: no auth so liveness probes work)
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        mode = mode_store.get()
    except Exception:  # noqa: BLE001
        mode = None
    return HealthResponse(ok=True, agent_mode=mode)


# ---------------------------------------------------------------------------
# Prometheus exposition.
#
# Mounted unauthenticated at /api/metrics AND /metrics so that:
#  - Operators with the bearer token pointing at /api/* still get it.
#  - A vanilla `ServiceMonitor` (Prometheus Operator) scraping /metrics
#    works without configuring an Authorization header.
#
# Both are in `_RATE_LIMIT_EXEMPT_PATHS` so scraping at 30 s intervals
# from many Prometheus replicas never triggers 429.
# ---------------------------------------------------------------------------

from fastapi.responses import Response  # noqa: E402 — local import keeps deps grouped


def _metrics_response() -> Response:
    from agent import metrics as _m

    # Refresh in-process gauges that aren't event-driven (NATS queue depth,
    # pending approvals count). Cheap; the scrape only fires every 15-60 s.
    try:
        from agent import approvals as _approvals

        _m.pending_approvals.set(len(_approvals.list_pending()))
    except Exception:  # noqa: BLE001
        pass
    try:
        _m.set_agent_mode(mode_store.get())
    except Exception:  # noqa: BLE001
        pass
    body, ct = _m.render_exposition()
    return Response(content=body, media_type=ct)


@app.get("/api/metrics", include_in_schema=False)
def metrics_api() -> Response:
    return _metrics_response()


@app.get("/metrics", include_in_schema=False)
def metrics_root() -> Response:
    return _metrics_response()


# ---------------------------------------------------------------------------
# Detailed system status — component-by-component reachability. Used by the
# SPA to show a precise reason when the cluster view is empty (instead of
# a generic "no pods"). Cheap: each check has a sub-second timeout, fails
# fast, never raises.
# ---------------------------------------------------------------------------


def _check_ollama() -> dict[str, Any]:
    """Are both the chat + embed models pulled?"""
    try:
        import httpx

        r = httpx.get(f"{settings.ollama_host}/api/tags", timeout=2.0)
        if r.status_code != 200:
            return {"ok": False, "reason": f"http {r.status_code}"}
        names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        chat = settings.chat_model.split(":")[0]
        embed = settings.embed_model.split(":")[0]
        missing = [m for m in (chat, embed) if m not in names]
        if missing:
            return {
                "ok": False,
                "reason": f"missing models: {missing}",
                "fix": "run 'make pull-models'",
            }
        return {"ok": True, "models": sorted(names)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc), "fix": "is the ollama service up?"}


def _check_qdrant() -> dict[str, Any]:
    """Is the runbooks collection seeded?"""
    try:
        import httpx

        r = httpx.get(
            f"{settings.qdrant_url}/collections/{settings.qdrant_collection}", timeout=2.0
        )
        if r.status_code == 404:
            return {"ok": False, "reason": "runbooks collection missing", "fix": "run 'make seed'"}
        if r.status_code != 200:
            return {"ok": False, "reason": f"http {r.status_code}"}
        points = (r.json().get("result", {}) or {}).get("points_count", 0)
        if points == 0:
            return {"ok": False, "reason": "collection exists but empty", "fix": "run 'make seed'"}
        return {"ok": True, "points": points}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc), "fix": "is qdrant up?"}


def _check_nats() -> dict[str, Any]:
    """Is JetStream reachable + the INFRA stream present?"""
    try:
        import asyncio

        async def probe():
            import nats as _nats

            nc = await asyncio.wait_for(_nats.connect(settings.nats_url), timeout=2.0)
            try:
                js = nc.jetstream()
                info = await asyncio.wait_for(js.stream_info("INFRA"), timeout=2.0)
                return {
                    "ok": True,
                    "messages": info.state.messages,
                    "last_seq": info.state.last_seq,
                }
            finally:
                await nc.drain()

        return asyncio.run(probe())
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": str(exc)[:120],
            "fix": "is nats up? has any event been published yet?",
        }


def _check_opa() -> dict[str, Any]:
    try:
        import httpx

        r = httpx.get(f"{settings.opa_url}/health", timeout=2.0)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


def _check_cluster() -> dict[str, Any]:
    """Can we reach the K8s API server?

    Tries in-cluster config first (production: agent runs as a Pod with
    a projected SA token), falls back to the dev kubeconfig file
    (laptop demo). Same precedence kubectl itself uses.
    """
    try:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(config_file=settings.kubeconfig)
        v1 = client.CoreV1Api()
        # 2s deadline; tiny call.
        nodes = v1.list_node(_request_timeout=2)
        return {
            "ok": True,
            "nodes": len(nodes.items),
            "first_node": nodes.items[0].metadata.name if nodes.items else None,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "reason": f"kubeconfig not found at {settings.kubeconfig}",
            "fix": "run 'make cluster' to create the k3d cluster",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": str(exc)[:150],
            "fix": "is the cluster up? try 'kubectl get nodes'",
        }


def _check_mode_cr() -> dict[str, Any]:
    try:
        m = mode_store.get()
        if m is None:
            return {
                "ok": False,
                "reason": "AgentMode CR not yet observed",
                "fix": "run 'make cluster' to apply the default CR",
            }
        return {"ok": True, "mode": m}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


@app.get("/api/health/detailed", dependencies=[Depends(require_bearer)])
def health_detailed() -> dict[str, Any]:
    """Full component-by-component status. Each sub-check is independent;
    a failure of one doesn't block the others."""
    components = {
        "ollama": _check_ollama(),
        "qdrant": _check_qdrant(),
        "nats": _check_nats(),
        "opa": _check_opa(),
        "cluster": _check_cluster(),
        "agent_mode": _check_mode_cr(),
    }
    all_ok = all(c.get("ok") for c in components.values())
    return {"ok": all_ok, "components": components}


# ---------------------------------------------------------------------------
# Inbound webhooks — external alert sources that produce NATS events the
# agent's graph picks up. Each ingress is auth'd by its own dedicated env
# var token so the SPA's bearer token (broad UI auth) isn't reused for
# narrow integration access.
# ---------------------------------------------------------------------------


def _check_webhook_token(header_value: str | None, env_name: str) -> bool:
    """Constant-time check against `os.environ[env_name]`. Returns False if
    the env var is unset (fail-closed), if the header is missing, or if
    they don't match."""
    import hmac as _hmac

    expected = (os.environ.get(env_name) or "").strip()
    if not expected:
        return False
    if not header_value:
        return False
    # Accept `Bearer <token>` and bare `<token>`.
    if header_value.startswith("Bearer "):
        header_value = header_value[len("Bearer ") :]
    return _hmac.compare_digest(header_value.strip(), expected)


@app.post("/api/webhooks/alertmanager")
async def alertmanager_webhook(request: Request) -> dict[str, Any]:
    """Receive Prometheus Alertmanager webhook notifications and turn each
    firing alert into a NATS `infra.events.alertmanager.<alertname>`
    message so the agent's existing graph consumer reacts.

    Auth: `ALERTMANAGER_WEBHOOK_TOKEN` env var. Fails-closed (503) if unset.
    Alertmanager configures the token via `http_config.authorization.credentials`
    or `basic_auth` — we accept both via the `Authorization` header.

    Payload shape (Alertmanager v4 — see
    https://prometheus.io/docs/alerting/latest/notifications/):

        {
          "version": "4",
          "groupKey": "...",
          "status": "firing"|"resolved",
          "alerts": [
            {
              "status": "firing",
              "labels": {"alertname": "PodHighMemory", "namespace": "workloads", "pod": "x"},
              "annotations": {"summary": "..."},
              "fingerprint": "abc123..."
            }
          ]
        }

    Only `status=firing` alerts produce graph runs (resolved alerts log + ack).
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not _check_webhook_token(auth, "ALERTMANAGER_WEBHOOK_TOKEN"):
        raise HTTPException(
            status_code=503,
            detail="alertmanager webhook not configured "
            "(set ALERTMANAGER_WEBHOOK_TOKEN env var) "
            "or bearer token rejected",
        )

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"malformed JSON: {exc}")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    alerts = body.get("alerts") or []
    if not isinstance(alerts, list):
        raise HTTPException(status_code=400, detail="`alerts` must be a list")

    import nats as _nats

    nc = await _nats.connect(settings.nats_url)
    js = nc.jetstream()
    try:
        await js.add_stream(name="INFRA", subjects=["infra.events.>"])
    except Exception:  # noqa: BLE001
        pass

    accepted = 0
    skipped: list[dict[str, Any]] = []
    try:
        for a in alerts:
            if not isinstance(a, dict):
                skipped.append({"reason": "alert not a dict"})
                continue
            if a.get("status") != "firing":
                skipped.append(
                    {"alertname": (a.get("labels") or {}).get("alertname"), "reason": "not firing"}
                )
                continue
            labels = a.get("labels") or {}
            annotations = a.get("annotations") or {}
            alertname = (labels.get("alertname") or "unknown_alert").strip()
            # NATS subject must be ASCII + dot-safe. Sanitize the alert name.
            subject_kind = "".join(c if c.isalnum() else "_" for c in alertname).lower()
            subject = f"infra.events.alertmanager.{subject_kind}"

            # Build a Falco-shaped event so the agent's existing triage
            # logic works without changes. Map alertmanager severity →
            # Falco priority; default to Warning.
            sev_map = {
                "critical": "Critical",
                "high": "Error",
                "warning": "Warning",
                "info": "Notice",
                "low": "Notice",
            }
            priority = sev_map.get((labels.get("severity") or "").lower(), "Warning")

            event = {
                "uuid": a.get("fingerprint") or "",
                "time": a.get("startsAt") or _now_iso(),
                "rule": alertname,
                "priority": priority,
                "source": "alertmanager",
                "output": (
                    annotations.get("summary")
                    or annotations.get("description")
                    or f"Alertmanager alert {alertname} is firing"
                )[:500],
                "output_fields": {
                    "event.kind": alertname,
                    "k8s.ns.name": labels.get("namespace", "") or "",
                    "k8s.pod.name": labels.get("pod", "") or "",
                    "k8s.deployment.name": labels.get("deployment", "") or "",
                    "k8s.node.name": labels.get("node") or labels.get("instance", "") or "",
                    "alert.fingerprint": a.get("fingerprint") or "",
                    "alert.severity": labels.get("severity", "") or "",
                    "alert.runbook_url": annotations.get("runbook_url", "") or "",
                    "alert.generator_url": a.get("generatorURL", "") or "",
                    **{f"label.{k}": str(v) for k, v in labels.items()},
                    **{f"annotation.{k}": str(v) for k, v in annotations.items()},
                },
            }
            await js.publish(subject, json.dumps(event).encode("utf-8"))
            accepted += 1
            log.info(
                "alertmanager: published %s (alertname=%s ns=%s pod=%s)",
                subject,
                alertname,
                labels.get("namespace"),
                labels.get("pod"),
            )
    finally:
        try:
            await nc.drain()
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "accepted": accepted,
        "skipped": len(skipped),
        "total": len(alerts),
        "skipped_detail": skipped[:5],  # cap so a flood doesn't return MBs
    }


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


@app.get("/api/mode", response_model=ModeResponse, dependencies=[Depends(require_bearer)])
def get_mode() -> ModeResponse:
    return ModeResponse(mode=mode_store.get())


@app.post("/api/mode", response_model=ModeResponse, dependencies=[Depends(require_bearer)])
def post_mode(body: ModeChangeRequest) -> ModeResponse:
    """Patch the AgentMode CRD. Returns 502 if kubectl fails."""
    patch = {"spec": {"mode": body.mode, "note": body.note or f"set via API at {_now_iso()}"}}
    p = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            settings.kubeconfig,
            "patch",
            "agentmode",
            settings.agent_mode_name,
            "--type=merge",
            "--patch",
            json.dumps(patch),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if p.returncode != 0:
        raise HTTPException(status_code=502, detail=f"kubectl patch failed: {p.stderr.strip()}")
    # Wait briefly for the watch to observe the change.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if mode_store.get() == body.mode:
            break
        time.sleep(0.2)
    return ModeResponse(mode=mode_store.get())


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


@app.get(
    "/api/cluster/nodes", response_model=list[NodeSummary], dependencies=[Depends(require_bearer)]
)
def list_nodes() -> list[NodeSummary]:
    items = _kubectl_get("nodes", "", "").get("items", [])
    out: list[NodeSummary] = []
    for n in items:
        md = n.get("metadata", {}) or {}
        st = n.get("status", {}) or {}
        nodeinfo = st.get("nodeInfo", {}) or {}
        conds = st.get("conditions", []) or []
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
        out.append(
            NodeSummary(
                name=md.get("name", ""),
                ready=ready,
                version=nodeinfo.get("kubeletVersion", "?"),
                taints=[t.get("key", "") for t in (n.get("spec", {}).get("taints") or [])],
            )
        )
    return out


# ─── Phase C — retention overlay endpoints ────────────────────────────────


def _retention_disk_usage() -> RetentionDiskUsage:
    """Walk the on-disk buckets the pruner manages. Best-effort; a
    missing directory returns zero rather than raising."""
    from pathlib import Path

    def _walk(directory: Path, pattern: str) -> tuple[int, int]:
        if not directory.exists():
            return (0, 0)
        total = 0
        count = 0
        for p in directory.glob(pattern):
            if p.is_file():
                try:
                    total += p.stat().st_size
                    count += 1
                except OSError:
                    pass
        return (total, count)

    audit_dir = Path(settings.audit_log_path).parent
    audit_bytes, audit_files = _walk(audit_dir, "audit.log*")
    incident_bytes, incident_files = _walk(Path("agent/incidents"), "inc-*.json")
    trace_bytes, trace_files = _walk(Path("agent/traces"), "inc-*.jsonl")

    # SQLite stores — single file each.
    def _size(path: str) -> int:
        try:
            return Path(path).stat().st_size
        except OSError:
            return 0

    cal_bytes = _size(os.environ.get("CALIBRATION_DB_PATH", "/data/calibration.db"))
    base_bytes = _size(os.environ.get("BASELINES_DB_PATH", "/data/baselines.db"))

    total = audit_bytes + incident_bytes + trace_bytes + cal_bytes + base_bytes
    return RetentionDiskUsage(
        audit_bytes=audit_bytes,
        audit_files=audit_files,
        incident_bytes=incident_bytes,
        incident_files=incident_files,
        trace_bytes=trace_bytes,
        trace_files=trace_files,
        calibration_bytes=cal_bytes,
        baselines_bytes=base_bytes,
        total_bytes=total,
    )


@app.get(
    "/api/retention",
    response_model=RetentionState,
    dependencies=[Depends(require_bearer)],
)
def get_retention() -> RetentionState:
    """Phase C — current retention config + on-disk usage. The SPA's
    Settings → Data Lifecycle tab renders this directly."""
    from agent.retention import get_default_store

    store = get_default_store()
    cfg = store.get()
    # `source` lets the SPA tell the operator whether their UI edit
    # is in effect (overlay) or we're still on env defaults.
    source = "overlay" if Path(store._path).exists() else "env"
    return RetentionState(
        days=RetentionDays(
            audit=cfg.audit_days,
            incidents=cfg.incident_days,
            traces=cfg.trace_days,
        ),
        sweep_interval_s=cfg.sweep_interval_s,
        source=source,
        disk_usage=_retention_disk_usage(),
        last_sweep_at=None,
        next_sweep_at=None,
    )


@app.put(
    "/api/retention",
    response_model=RetentionState,
    dependencies=[Depends(require_bearer)],
)
def put_retention(body: RetentionUpdateRequest) -> RetentionState:
    """Live-update retention. The pruner reads from this store on
    each tick so the new values apply at the NEXT sweep — no pod
    restart needed.

    Set `reset: true` to delete the overlay and revert to env
    defaults (also useful when the operator wants to 'try out' a
    setting and back out cleanly)."""
    from agent.retention import RetentionConfig, get_default_store

    store = get_default_store()
    if body.reset:
        store.reset_to_defaults()
        return get_retention()

    current = store.get()
    days = body.days or RetentionDays(
        audit=current.audit_days,
        incidents=current.incident_days,
        traces=current.trace_days,
    )
    sweep = body.sweep_interval_s if body.sweep_interval_s is not None else current.sweep_interval_s
    cfg = RetentionConfig(
        audit_days=days.audit,
        incident_days=days.incidents,
        trace_days=days.traces,
        sweep_interval_s=sweep,
    )
    store.put(cfg)
    # Audit-trail this operator action — useful when forensic-ing why
    # an old audit file disappeared.
    _audit.emit(
        "system",
        "retention.update",
        {
            "audit_days": cfg.audit_days,
            "incident_days": cfg.incident_days,
            "trace_days": cfg.trace_days,
            "sweep_interval_s": cfg.sweep_interval_s,
        },
    )
    return get_retention()


@app.get(
    "/api/cluster/context",
    response_model=ClusterContext,
    dependencies=[Depends(require_bearer)],
)
def cluster_context() -> ClusterContext:
    """Phase A — one-shot snapshot of the host cluster's identity +
    every node + every well-known operator we can detect. Used by:
      - dashboard's 'Cluster context' card (live)
      - first-time-setup wizard (one-time, on first login)
      - Settings → Cluster context tab (re-probe on demand)
    Best-effort: every field has a sane default if the underlying
    kubectl call fails, so the endpoint always returns 200."""
    nodes_raw = _kubectl_get("nodes", "", "").get("items", [])

    # Pull the agent's own placement from the downward API env vars
    # we set in the Helm Deployment template. Outside a K8s pod
    # (laptop demo via docker-compose) these are empty.
    agent_self = AgentSelf(
        namespace=os.environ.get("KEOS_POD_NAMESPACE", ""),
        pod=os.environ.get("KEOS_POD_NAME", ""),
        node=os.environ.get("KEOS_NODE_NAME", ""),
    )

    # Per-node context. The role heuristic: nodes with the
    # `node-role.kubernetes.io/control-plane` or `master` label are
    # control-plane; everything else is "worker". `is_agent_home` is
    # true when the node name matches agent_self.node.
    nodes: list[NodeContext] = []
    total_cpu_m = 0
    total_mem_bytes = 0
    total_gpu = 0
    ready_count = 0
    kube_version = ""
    runtime = "unknown"
    for n in nodes_raw:
        md = n.get("metadata", {}) or {}
        spec = n.get("spec", {}) or {}
        st = n.get("status", {}) or {}
        info = st.get("nodeInfo", {}) or {}
        cap = st.get("capacity", {}) or {}
        labels = md.get("labels", {}) or {}
        conds = st.get("conditions", []) or []
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
        if ready:
            ready_count += 1
        if not kube_version:
            kube_version = info.get("kubeletVersion", "")
        if runtime == "unknown":
            cri = info.get("containerRuntimeVersion", "")
            if cri.startswith("containerd"):
                runtime = "containerd"
            elif cri.startswith("cri-o"):
                runtime = "crio"
            elif cri.startswith("docker"):
                runtime = "docker"
        # CPU capacity comes as a string like "8" or "4500m"; normalize to millicores.
        cpu_str = str(cap.get("cpu", "0"))
        cpu_m = (
            int(cpu_str[:-1])
            if cpu_str.endswith("m")
            else int(float(cpu_str) * 1000)
            if cpu_str
            else 0
        )
        # Memory: "16384528Ki" / "16Gi"; normalize to bytes.
        mem_bytes = _parse_k8s_memory(str(cap.get("memory", "0")))
        gpu = int(cap.get("nvidia.com/gpu", 0) or 0)
        total_cpu_m += cpu_m
        total_mem_bytes += mem_bytes
        total_gpu += gpu
        role = (
            "control-plane"
            if "node-role.kubernetes.io/control-plane" in labels
            or "node-role.kubernetes.io/master" in labels
            else "worker"
        )
        nodes.append(
            NodeContext(
                name=md.get("name", ""),
                role=role,
                ready=ready,
                is_agent_home=(md.get("name") == agent_self.node) if agent_self.node else False,
                cpu_capacity_m=cpu_m or None,
                memory_capacity_bytes=mem_bytes or None,
                gpu_capacity=gpu or None,
                labels=labels,
                taints=[t.get("key", "") for t in (spec.get("taints") or [])],
            )
        )

    # Distro heuristic. Order: k3s label > k3d (label key on first
    # node) > Talos (kube-system Deployment name) > RKE2 > kubeadm.
    distro = _detect_distro(nodes_raw, kube_version)

    # Detected operators — well-known CRDs / kube-system Deployments.
    ops = _detect_operators()

    # Warnings: things that AREN'T errors but matter for ops.
    warnings: list[str] = []
    if not ops.argocd and not ops.flux:
        warnings.append(
            "No GitOps controller detected (Argo CD / Flux). Drift detection "
            "will fall back to filesystem-mounted manifests."
        )
    if not ops.prometheus:
        warnings.append(
            "Prometheus not detected. Set PROMETHEUS_URL or install "
            "kube-prometheus-stack to enable baselines + forecasts."
        )
    if not ops.chaos_mesh:
        warnings.append(
            "Chaos Mesh not detected. Use Case wizard's chaos experiments "
            "won't work until it's installed."
        )
    if agent_self.node and not any(n.is_agent_home for n in nodes):
        warnings.append(
            f"Agent reports it's on node {agent_self.node!r} but that "
            "node isn't in the cluster's node list — odd; check the kubeconfig."
        )

    return ClusterContext(
        distro=distro,
        runtime=runtime,
        kubernetes_version=kube_version or "?",
        node_count=len(nodes),
        ready_node_count=ready_count,
        total_cpu_m=total_cpu_m,
        total_memory_bytes=total_mem_bytes,
        total_gpu_count=total_gpu,
        nodes=nodes,
        detected_operators=ops,
        agent_self=agent_self,
        warnings=warnings,
    )


def _parse_k8s_memory(s: str) -> int:
    """Best-effort parser for the K8s memory capacity strings:
    '16384528Ki' / '16Gi' / '8589934592' (raw bytes). Returns 0 on
    failure rather than raising; the endpoint always succeeds."""
    if not s:
        return 0
    multipliers = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    try:
        for suffix, mult in multipliers.items():
            if s.endswith(suffix):
                return int(float(s[: -len(suffix)]) * mult)
        return int(s)
    except (ValueError, TypeError):
        return 0


def _detect_distro(nodes_raw: list[dict], kube_version: str) -> str:
    """Best-effort K8s-distro fingerprinting. Mostly cosmetic — the
    setup wizard uses it to pick sensible defaults (containerd socket
    path on Talos, NetworkPolicy enforcement on Calico clusters, etc.)."""
    # k3s embeds itself into the kubelet version string.
    if "k3s" in kube_version.lower():
        # k3d nodes have a label `k3d.io/node-name`; k3s does not.
        for n in nodes_raw:
            labels = (n.get("metadata", {}) or {}).get("labels", {}) or {}
            if any(k.startswith("k3d.io/") for k in labels):
                return "k3d"
        return "k3s"
    if "rke2" in kube_version.lower():
        return "rke2"
    # Talos: every node has the `node.kubernetes.io/instance-type=talos.dev` label.
    for n in nodes_raw:
        labels = (n.get("metadata", {}) or {}).get("labels", {}) or {}
        if any("talos" in k.lower() or "talos" in str(v).lower() for k, v in labels.items()):
            return "talos"
    # EKS / GKE / AKS: identifiable via provider-specific labels.
    for n in nodes_raw:
        labels = (n.get("metadata", {}) or {}).get("labels", {}) or {}
        if any(k.startswith("eks.amazonaws.com/") for k in labels):
            return "eks"
        if any(k.startswith("cloud.google.com/gke-") for k in labels):
            return "gke"
        if any(k.startswith("kubernetes.azure.com/") for k in labels):
            return "aks"
    # If nothing matched and we can talk to the API, call it kubeadm.
    return "kubeadm" if kube_version else "unknown"


def _deployment_exists(name: str, namespace: str) -> bool:
    """True if a Deployment OR StatefulSet with this name exists in
    the namespace. List-and-filter rather than `kubectl get deploy <n>`
    because the single-resource form returns a bare object (no `items`)
    on success AND a non-zero exit on missing — both shapes our generic
    `_kubectl_get` helper turns into an empty `items` list, making
    success and absence indistinguishable.

    Checking Deployment OR StatefulSet covers both `prometheus-server`
    (community Helm chart, Deployment) and `prometheus-k8s`
    (Prometheus Operator, StatefulSet)."""
    for kind in ("deployments", "statefulsets"):
        items = _kubectl_get(kind, "", namespace).get("items", [])
        for it in items:
            if (it.get("metadata") or {}).get("name") == name:
                return True
    return False


def _detect_operators() -> DetectedOperators:
    """Probe for well-known operators with a layered approach:
      1. CRDs   — works for operator-driven installs (Prometheus Operator,
                 Argo CD, Flux v2, cert-manager, Chaos Mesh).
      2. Deployments / StatefulSets — works for standalone installs that
                 ship a workload but no CRDs (plain Prometheus from the
                 community Helm chart, standalone Argo Workflows, etc.).
    Each probe is independent — failure of one doesn't bias the others."""
    crd_probes = {
        "argocd": "applications.argoproj.io",
        "flux": "kustomizations.kustomize.toolkit.fluxcd.io",
        "chaos_mesh": "podchaos.chaos-mesh.org",
        "prometheus": "prometheuses.monitoring.coreos.com",
        "cert_manager": "certificates.cert-manager.io",
    }
    # Per-operator workload probes — (field, name, candidate_namespaces).
    # Order matters; first match wins. Standalone installs almost always
    # land in `monitoring`; Helm umbrella charts sometimes put them in
    # their release namespace.
    workload_probes: list[tuple[str, str, list[str]]] = [
        # Standalone Prometheus (community Helm chart) → Deployment 'prometheus'.
        # Prometheus Operator → StatefulSet 'prometheus-k8s' or 'prometheus-prometheus'.
        ("prometheus", "prometheus", ["monitoring", "prometheus", "observability"]),
        ("prometheus", "prometheus-server", ["monitoring", "prometheus"]),
        ("prometheus", "prometheus-k8s", ["monitoring"]),
        # Argo CD server (no CRD = unusual but possible).
        ("argocd", "argocd-server", ["argocd"]),
        # Flux's source-controller is the canonical "Flux installed" signal.
        ("flux", "source-controller", ["flux-system"]),
        # cert-manager's webhook Deployment.
        ("cert_manager", "cert-manager-webhook", ["cert-manager"]),
        # Chaos Mesh controller (in case CRDs missed — rare).
        ("chaos_mesh", "chaos-controller-manager", ["chaos-mesh"]),
    ]
    out = DetectedOperators()
    crds = _kubectl_get("customresourcedefinitions", "", "").get("items", [])
    crd_names = {(c.get("metadata", {}) or {}).get("name", "") for c in crds}
    for field, name in crd_probes.items():
        if name in crd_names:
            setattr(out, field, True)
    # Workload fallbacks — only run if the CRD probe didn't already
    # set the field, to avoid wasted API calls on operator-style installs.
    for field, workload_name, ns_candidates in workload_probes:
        if getattr(out, field):
            continue
        for ns in ns_candidates:
            try:
                if _deployment_exists(workload_name, ns):
                    setattr(out, field, True)
                    break
            except Exception:  # noqa: BLE001
                pass
    # kube-state-metrics is a Deployment (not a CRD). Match any name
    # ending in "kube-state-metrics" — the kube-prometheus-stack chart
    # prefixes the release name (e.g. "prom-kube-state-metrics"), and
    # other distributions may add their own suffix/prefix.
    try:
        for ns in ("monitoring", "kube-system"):
            items = _kubectl_get("deployments", "", ns).get("items", [])
            for d in items:
                dname = ((d.get("metadata") or {}).get("name") or "").lower()
                if "kube-state-metrics" in dname:
                    out.kube_state_metrics = True
                    break
            if out.kube_state_metrics:
                break
    except Exception:  # noqa: BLE001
        pass
    return out


@app.get(
    "/api/cluster/namespaces",
    response_model=list[NamespaceSummary],
    dependencies=[Depends(require_bearer)],
)
def list_namespaces() -> list[NamespaceSummary]:
    items = _kubectl_get("namespaces", "", "").get("items", [])
    out: list[NamespaceSummary] = []
    for n in items:
        md = n.get("metadata", {}) or {}
        ns_name = md.get("name", "")
        age = _age_seconds(md.get("creationTimestamp"))
        pods = _kubectl_get("pods", "", ns_name).get("items", [])
        out.append(NamespaceSummary(name=ns_name, age_seconds=age, pod_count=len(pods)))
    return out


@app.get(
    "/api/cluster/deployments",
    response_model=list[DeploymentSummary],
    dependencies=[Depends(require_bearer)],
)
def list_deployments(ns: str = Query("workloads")) -> list[DeploymentSummary]:
    items = _kubectl_get("deployments", "", ns).get("items", [])
    out: list[DeploymentSummary] = []
    for d in items:
        md = d.get("metadata", {}) or {}
        spec = d.get("spec", {}) or {}
        st = d.get("status", {}) or {}
        containers = ((spec.get("template", {}) or {}).get("spec", {}) or {}).get(
            "containers", []
        ) or []
        images = [c.get("image", "") for c in containers if c.get("image")]
        desired = int(spec.get("replicas", 0) or 0)
        ready = int(st.get("readyReplicas", 0) or 0)
        updated = int(st.get("updatedReplicas", 0) or 0)
        out.append(
            DeploymentSummary(
                name=md.get("name", ""),
                namespace=md.get("namespace", ns),
                replicas_desired=desired,
                replicas_ready=ready,
                replicas_updated=updated,
                age_seconds=_age_seconds(md.get("creationTimestamp")),
                images=images,
                available=ready >= desired and desired > 0,
            )
        )
    return out


@app.get(
    "/api/cluster/events", response_model=list[EventSummary], dependencies=[Depends(require_bearer)]
)
def list_events(ns: str = Query("workloads"), limit: int = 50) -> list[EventSummary]:
    items = _kubectl_get("events", "", ns).get("items", [])
    out: list[EventSummary] = []
    for e in items:
        md = e.get("metadata", {}) or {}
        inv = e.get("involvedObject", {}) or {}
        out.append(
            EventSummary(
                namespace=md.get("namespace", ns),
                name=md.get("name", ""),
                type=e.get("type", "Normal"),
                reason=e.get("reason", ""),
                message=e.get("message", ""),
                involved_kind=inv.get("kind"),
                involved_name=inv.get("name"),
                count=int(e.get("count", 1) or 1),
                first_seen=e.get("firstTimestamp") or e.get("eventTime"),
                last_seen=e.get("lastTimestamp") or e.get("eventTime"),
            )
        )
    # Newest first by lastSeen.
    out.sort(key=lambda x: x.last_seen or "", reverse=True)
    return out[:limit]


@app.get(
    "/api/cluster/pods", response_model=list[PodSummary], dependencies=[Depends(require_bearer)]
)
def list_pods(ns: str = Query("workloads")) -> list[PodSummary]:
    items = _kubectl_get("pods", "", ns).get("items", [])
    out: list[PodSummary] = []
    for p in items:
        md = p.get("metadata", {}) or {}
        st = p.get("status", {}) or {}
        cs = st.get("containerStatuses", []) or []
        ready = sum(1 for c in cs if c.get("ready"))
        restarts = sum(int(c.get("restartCount", 0) or 0) for c in cs)
        owner = (md.get("ownerReferences") or [{}])[0]
        out.append(
            PodSummary(
                name=md.get("name", ""),
                namespace=md.get("namespace", ""),
                phase=st.get("phase", "Unknown"),
                ready=f"{ready}/{len(cs)}" if cs else "0/0",
                restarts=restarts,
                age_seconds=_age_seconds(md.get("creationTimestamp")),
                owner_kind=owner.get("kind"),
                owner_name=owner.get("name"),
                last_event=None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@app.get(
    "/api/incidents/active",
    response_model=list[ActiveIncident],
    dependencies=[Depends(require_bearer)],
)
def list_active_incidents() -> list[ActiveIncident]:
    """Incidents whose graph is still running (a `pipeline.*.start` was
    written but no `complete` audit yet). Used by the SPA to populate the
    'attach to incident' picker on /pipeline/live."""
    active_ids = trace_bus.list_active()
    out: list[ActiveIncident] = []
    for inc_id in active_ids:
        # Pull a quick summary from the trace file if we have it.
        from agent.pipeline_trace import replay as trace_replay

        rule: str | None = None
        severity: str | None = None
        pod: str | None = None
        namespace: str | None = None
        started_at: str | None = None
        for ev in trace_replay(inc_id):
            if started_at is None:
                started_at = ev.get("ts")
            snap = ev.get("output_snapshot") or ev.get("input_snapshot") or {}
            if isinstance(snap, dict):
                rule = rule or snap.get("rule")
                severity = severity or snap.get("severity")
                pod = pod or snap.get("pod")
                namespace = namespace or snap.get("namespace")
            if rule and severity and pod and namespace:
                break
        out.append(
            ActiveIncident(
                incident_id=inc_id,
                rule=rule,
                severity=severity,
                pod=pod,
                namespace=namespace,
                started_at=started_at,
            )
        )
    # Newest first.
    out.sort(key=lambda x: x.started_at or "", reverse=True)
    return out


@app.get("/api/incidents", response_model=IncidentList, dependencies=[Depends(require_bearer)])
def list_incidents(
    namespace: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    since: str | None = None,
    pod: str | None = Query(None, description="Filter to incidents touching this pod (Phase 14.5)"),
    rule: str | None = Query(
        None, description="Filter to incidents matching this rule (Phase 14.5)"
    ),
    limit: int = 50,
    offset: int = 0,
) -> IncidentList:
    items = incident_store.list_all()
    total, page = incident_store.filter_and_paginate(
        items,
        namespace=namespace,
        severity=severity,
        status=status,
        since=since,
        pod=pod,
        rule=rule,
        limit=limit,
        offset=offset,
    )
    # Defensive: a single persisted incident that uses a tool name removed in
    # a later release (e.g. isolate_pod from the pre-SRE-pivot era) would
    # otherwise 500 the whole endpoint. Skip + log instead.
    summaries: list[IncidentSummary] = []
    for it in page:
        try:
            summaries.append(IncidentSummary.model_validate(incident_store.summarise(it)))
        except Exception as exc:  # noqa: BLE001
            log.warning("incident summary skipped for id=%s — %s", it.get("incident_id", "?"), exc)
    return IncidentList(total=total, items=summaries)


@app.get(
    "/api/incidents/{incident_id}",
    response_model=IncidentDetail,
    dependencies=[Depends(require_bearer)],
)
def get_incident(incident_id: str) -> IncidentDetail:
    inc = incident_store.load(incident_id)
    if inc is None:
        # Graph hasn't finalized yet — but a trace file may already exist
        # for an in-flight run (CPU LLM cold-load takes 5-10 min on the
        # first scenario). Surface whatever we have so the SPA can render
        # the live timeline. Fields the SPA needs from the trace records
        # are derived from the first `retrieve_context.start` event's
        # `input_snapshot` (severity, namespace, pod, rule).
        trace = list(pipeline_trace.replay(incident_id))
        if not trace:
            raise HTTPException(status_code=404, detail="incident not found")
        snap = next(
            (
                ev.get("input_snapshot") or {}
                for ev in trace
                if ev.get("phase") == "start" and (ev.get("input_snapshot") or {})
            ),
            {},
        )
        return IncidentDetail(
            incident_id=incident_id,
            ts=trace[0].get("ts") or _now_iso(),
            rule=snap.get("rule") or "(in progress)",
            severity=snap.get("severity") or "medium",
            namespace=snap.get("namespace"),
            pod=snap.get("pod"),
            agent_mode=snap.get("agent_mode"),
            escalated=False,
            stale_resolved=False,
            pipeline_trace=trace,
        )
    summary = incident_store.summarise(inc)
    # Coerce obsolete tool names so Pydantic Literal validation doesn't 500
    # on incidents persisted before the SRE pivot.
    _DROPPED_TOOLS = {"isolate_pod", "apply_network_policy"}
    if summary.get("proposed_tool") in _DROPPED_TOOLS:
        summary["proposed_tool"] = "page_human"
        summary["_migrated_from"] = inc.get("proposed_action", {}).get("tool")
    trace = list(pipeline_trace.replay(incident_id))
    return IncidentDetail(
        **summary,
        raw_event=inc.get("raw_event"),
        runbook_hits=(inc.get("context") or {}).get("runbook_hits", []),
        k8sgpt_findings=inc.get("k8sgpt_findings") or [],
        diagnosis=inc.get("diagnosis"),
        proposed_action=inc.get("proposed_action"),
        policy_decision=inc.get("policy_decision"),
        action_result=inc.get("action_result"),
        verification=inc.get("verification"),
        pipeline_trace=trace,
    )


@app.delete("/api/incidents/{incident_id}", dependencies=[Depends(require_bearer)])
def delete_incident(incident_id: str) -> dict[str, Any]:
    """Delete a single incident + its trace file. Idempotent — returns 404
    if the incident doesn't exist so the UI can show a clean error."""
    if not incident_store.delete(incident_id):
        raise HTTPException(status_code=404, detail="incident not found")
    return {"ok": True, "deleted": incident_id}


@app.post("/api/incidents/bulk-delete", dependencies=[Depends(require_bearer)])
def bulk_delete_incidents(body: dict[str, Any]) -> dict[str, Any]:
    """Bulk delete. POST instead of DELETE because some proxies strip
    bodies from DELETE requests. Body shape:
        {"ids": ["uc-xxxx", "inc-yyyy"], "only_healed": false}

    `only_healed=true` filters server-side to incidents whose verification
    has `healed=true` — a safety net so the UI can offer a "clear all
    healed" affordance without having to filter client-side."""
    ids = body.get("ids", []) or []
    only_healed = bool(body.get("only_healed"))
    if only_healed:
        # Re-read each incident to confirm healed status.
        ok_ids: list[str] = []
        for inc_id in ids:
            inc = incident_store.load(inc_id)
            if inc and (inc.get("verification") or {}).get("healed") is True:
                ok_ids.append(inc_id)
        ids = ok_ids
    result = incident_store.delete_many(ids)
    return {"ok": True, **result, "requested": len(body.get("ids", []) or [])}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@app.get("/api/audit", response_model=AuditList, dependencies=[Depends(require_bearer)])
def get_audit(
    since: str | None = None, limit: int = 50, incident_id: str | None = None
) -> AuditList:
    path = Path(settings.audit_log_path)
    if not path.exists():
        return AuditList(total=0, items=[])
    records: list[AuditRecord] = []
    key = settings.audit_hmac_key.encode()
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if since and rec.get("ts", "") < since:
                continue
            if incident_id and rec.get("incident_id") != incident_id:
                continue
            records.append(
                AuditRecord(
                    ts=rec.get("ts"),
                    incident_id=rec.get("incident_id", ""),
                    event_type=rec.get("event_type", ""),
                    payload=rec.get("payload", {}),
                    hmac=rec.get("hmac", ""),
                    hmac_valid=_verify_record(rec, key),
                )
            )
    total = len(records)
    return AuditList(total=total, items=records[-limit:])


@app.post(
    "/api/audit/verify", response_model=AuditVerifyResponse, dependencies=[Depends(require_bearer)]
)
def audit_verify() -> AuditVerifyResponse:
    n, invalid = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
    return AuditVerifyResponse(valid=(n > 0 and not invalid))


# ---------------------------------------------------------------------------
# Scenarios (built-in)
# ---------------------------------------------------------------------------


@app.get(
    "/api/scenarios",
    response_model=list[ScenarioDescriptor],
    dependencies=[Depends(require_bearer)],
)
def list_scenarios() -> list[ScenarioDescriptor]:
    descriptors: list[ScenarioDescriptor] = []
    for sid, subject in synthetic_events._SUBJECTS.items():
        ev = synthetic_events.build_event(sid)
        descriptors.append(
            ScenarioDescriptor(
                id=sid,
                rule=ev["rule"],
                priority=ev["priority"],
                subject=subject,
                description=ev.get("output", "")[:200],
            )
        )
    return descriptors


@app.post(
    "/api/scenarios/run", response_model=ScenarioRunResponse, dependencies=[Depends(require_bearer)]
)
def run_scenario(body: ScenarioRunRequest) -> ScenarioRunResponse:
    import asyncio as _asyncio

    if body.scenario_id not in synthetic_events._SUBJECTS:
        raise HTTPException(status_code=404, detail="unknown scenario")
    subject = synthetic_events._SUBJECTS[body.scenario_id]
    event = synthetic_events.build_event(body.scenario_id)
    # Apply overrides if any (pod, namespace).
    if body.overrides:
        of = event.setdefault("output_fields", {})
        for k in ("k8s.ns.name", "k8s.pod.name", "container.name"):
            override_key = k.split(".")[-1]
            if override_key in body.overrides:
                of[k] = body.overrides[override_key]
    incident_id = event.setdefault("incident_id", f"inc-{os.urandom(5).hex()}")
    _asyncio.run(_publish_event(subject, event))
    return ScenarioRunResponse(incident_id=incident_id, subject=subject)


async def _publish_event(subject: str, payload: dict[str, Any]) -> None:
    import nats

    nc = await nats.connect(settings.nats_url)
    try:
        js = nc.jetstream()
        try:
            await js.add_stream(name="INFRA", subjects=["infra.events.>"])
        except Exception:  # noqa: BLE001
            pass
        await js.publish(subject, json.dumps(payload).encode())
    finally:
        await nc.drain()


# ---------------------------------------------------------------------------
# Calibration (Phase 12)
# ---------------------------------------------------------------------------


def _rule_stats_to_model(stats: Any) -> CalibrationRule:
    """Project a `RuleStats` dataclass into the public API model."""
    return CalibrationRule(
        rule_id=stats.rule_id,
        n_total=stats.n_total,
        n_graded=stats.n_graded,
        n_correct=stats.n_correct,
        n_escalated_lowconf=stats.n_escalated_lowconf,
        n_escalated_other=stats.n_escalated_other,
        n_stale=stats.n_stale,
        n_override=stats.n_override,
        accuracy=stats.accuracy,
        mean_confidence=stats.mean_confidence,
        p50_confidence=stats.p50_confidence,
        p90_confidence=stats.p90_confidence,
        threshold_override=stats.threshold_override,
        learned_threshold=stats.learned_threshold,
        effective_threshold=stats.effective_threshold,
        updated_at=stats.updated_at,
    )


@app.get(
    "/api/calibration",
    response_model=CalibrationList,
    dependencies=[Depends(require_bearer)],
)
def list_calibration() -> CalibrationList:
    """All rules the agent has seen, with per-rule confidence stats."""
    from agent.calibration import get_default_store

    items = [_rule_stats_to_model(s) for s in get_default_store().list_all()]
    # Cluster-wide aggregates so the SPA can render a "where are we vs 90 %"
    # banner without re-computing client-side.
    median = None
    pct_above_90 = None
    # Include every rule the agent has actually sampled (at least one
    # diagnose call). Earlier we tried to filter out rules with
    # mean_confidence == 0 and n_diagnosed < 3 thinking these were
    # 'noise' that pulled the median down. But on a fresh install
    # those filters drop EVERY rule, leaving the dashboard with a
    # bare "—" and no signal at all. Reality: a conf=0 sample is a
    # real measurement (the LLM said "I'm 0% sure"). If the median IS
    # low, the operator deserves to see that — it tells them the
    # agent isn't ready to act autonomously yet.
    all_samples: list[float] = []
    for it in items:
        if it.mean_confidence is not None:
            all_samples.append(it.mean_confidence)
    if all_samples:
        ordered = sorted(all_samples)
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        above = sum(1 for x in ordered if x >= 0.9)
        pct_above_90 = above / len(ordered)
    return CalibrationList(
        total=len(items),
        items=items,
        median_confidence=median,
        pct_above_90=pct_above_90,
    )


@app.get(
    "/api/calibration/{rule_id}",
    response_model=CalibrationRule,
    dependencies=[Depends(require_bearer)],
)
def get_calibration_rule(rule_id: str) -> CalibrationRule:
    from agent.calibration import get_default_store

    stats = get_default_store().get(rule_id)
    return _rule_stats_to_model(stats)


@app.post(
    "/api/calibration/{rule_id}/threshold",
    response_model=CalibrationRule,
    dependencies=[Depends(require_bearer)],
)
def set_calibration_threshold(rule_id: str, req: CalibrationThresholdRequest) -> CalibrationRule:
    """Pin (or clear, with `threshold=null`) the operator override for a
    rule. Persisted to the SQLite store; survives agent restart."""
    from agent.calibration import get_default_store

    store = get_default_store()
    store.set_threshold_override(rule_id, req.threshold)
    _audit.emit(
        f"calibration-{rule_id}",
        "calibration.threshold_override",
        {
            "rule_id": rule_id,
            "threshold": req.threshold,
            "note": req.note,
        },
    )
    return _rule_stats_to_model(store.get(rule_id))


# ---------------------------------------------------------------------------
# Baselines (Phase 13) — per-pod / per-metric snapshots for the SPA + scripts.
# ---------------------------------------------------------------------------


def _snapshot_to_model(s: dict) -> BaselineSnapshotModel:
    """Pure dict → pydantic projection. Kept tiny so callers compose."""
    return BaselineSnapshotModel(
        metric=str(s.get("metric", "")),
        namespace=str(s.get("namespace", "")),
        scope=str(s.get("scope", "")),
        status=str(s.get("status", "missing")),
        current=s.get("current"),
        mean=s.get("mean"),
        std=s.get("std"),
        sigma_above=s.get("sigma_above"),
        seasonal_residual=s.get("seasonal_residual"),
        n_samples=int(s.get("n_samples", 0)),
    )


@app.get(
    "/api/baselines",
    response_model=BaselineList,
    dependencies=[Depends(require_bearer)],
)
def list_baselines(
    ns: str | None = Query(None, description="Namespace filter; required when pod is set."),
    pod: str | None = Query(None, description="Pod name; returns the diagnose-time snapshot list."),
) -> BaselineList:
    """Read view over the baselines store.

    With both `ns` and `pod` set, returns the same top-N snapshots the
    `enrich_with_baselines` node would inject into the diagnose prompt.
    Without filters, returns every known key as a `status=missing`
    placeholder (the SPA's "what is the learner seeing?" page).
    """
    from agent.baselines import get_default_store, snapshot_for_pod
    from agent.baselines.snapshot import snapshot_one

    store = get_default_store()
    if ns and pod:
        snaps = snapshot_for_pod(ns, pod, store=store)
        items = [_snapshot_to_model(s.to_dict()) for s in snaps]
    else:
        # Enumerate every known key. Cheap because list_keys returns
        # at most a few hundred rows on edge clusters.
        keys = store.list_keys()
        items = []
        for key in keys:
            recent = store.recent_samples(key, limit=1)
            current = recent[-1].value if recent else None
            fit = store.get_fit(key)
            snap = snapshot_one(key, current, fit)
            items.append(_snapshot_to_model(snap.to_dict()))
    return BaselineList(
        total=len(items),
        items=items,
        total_samples=store.count_samples(),
        keys_known=len(store.list_keys()),
    )


# ---------------------------------------------------------------------------
# Forecasts (Phase 14.5) — per-pod horizon projections for the SPA.
# Reuses the Phase 14 forecaster math + the Phase 13 baseline store.
# ---------------------------------------------------------------------------


@app.get(
    "/api/forecasts",
    response_model=ForecastList,
    dependencies=[Depends(require_bearer)],
)
def list_forecasts(
    ns: str | None = Query(None, description="Namespace filter; required when pod is set."),
    pod: str | None = Query(None, description="Pod name; returns one forecast row per MetricKey."),
) -> ForecastList:
    """Per-pod (or all-keys) forecast snapshots for the SPA's
    workload-drill-down "Forecasts" tab + the live-pipeline scene.

    Conservatively returns level=`none` for keys that fit OK but
    don't trip the danger classifier, so the SPA can render the
    full picture (a row per key) and not just the anomalous ones."""
    from agent.baselines import get_default_store
    from agent.baselines.store import MetricKey
    from agent.forecasting import decide, fit_linear

    store = get_default_store()
    keys: list[MetricKey] = (
        store.list_keys(namespace=ns, scope=pod) if (ns and pod) else store.list_keys()
    )
    rows: list[ForecastRow] = []
    for key in keys:
        fit_row = store.get_fit(key)
        samples = store.recent_samples(key, limit=60)
        forecast = fit_linear([(s.ts, s.value) for s in samples])
        current = samples[-1].value if samples else None
        if forecast is None or fit_row is None or fit_row.std < 1e-6:
            rows.append(
                ForecastRow(
                    metric=key.metric,
                    namespace=key.namespace,
                    scope=key.scope,
                    level="none",
                    current=current,
                    mean=fit_row.mean if fit_row else None,
                    std=fit_row.std if fit_row else None,
                    n_samples=fit_row.n_samples if fit_row else 0,
                )
            )
            continue
        decision = decide(forecast, fit_row.mean, fit_row.std)
        rows.append(
            ForecastRow(
                metric=key.metric,
                namespace=key.namespace,
                scope=key.scope,
                level=decision.level,
                slope_per_s=forecast.slope_per_s,
                r2=forecast.r2,
                t5_min=forecast.t5_min,
                t15_min=forecast.t15_min,
                t60_min=forecast.t60_min,
                sigma_above=decision.sigma_above,
                current=current,
                mean=fit_row.mean,
                std=fit_row.std,
                n_samples=fit_row.n_samples,
            )
        )
    # Sort: anomalous first (critical → warning → watch → safe → none),
    # then by descending |sigma_above|. Matches the SPA expectation.
    level_rank = {"critical": 0, "warning": 1, "watch": 2, "safe": 3, "none": 4}
    rows.sort(key=lambda r: (level_rank.get(r.level, 9), -abs(r.sigma_above or 0.0)))
    return ForecastList(total=len(rows), items=rows)


# ---------------------------------------------------------------------------
# Scene (Phase 14.5) — what the agent DID during one incident.
# Aggregates: resources touched, the action chain, the outcome banner.
# ---------------------------------------------------------------------------


def _scene_from_incident(incident: dict[str, object]) -> WorkloadScene:
    """Pure projection — runs over the persisted incident snapshot
    that `incident_store.save()` writes. Kept pure so the SPA test
    suite can drive it without a live agent."""
    inc_id = str(incident.get("incident_id", ""))
    ns = incident.get("namespace") if isinstance(incident.get("namespace"), str) else None
    pod = incident.get("pod") if isinstance(incident.get("pod"), str) else None
    # ---- Outcome banner ------------------------------------------------
    verification = incident.get("verification") or {}
    if not isinstance(verification, dict):
        verification = {}
    proposed = incident.get("proposed_action") or {}
    if not isinstance(proposed, dict):
        proposed = {}
    diagnosis = incident.get("diagnosis") or {}
    if not isinstance(diagnosis, dict):
        diagnosis = {}
    decision = incident.get("policy_decision") or {}
    if not isinstance(decision, dict):
        decision = {}
    escalated = bool(incident.get("escalated"))
    healed = verification.get("healed")
    mode = incident.get("agent_mode")
    tool = proposed.get("tool") if isinstance(proposed.get("tool"), str) else None
    action_result_raw = incident.get("action_result") or {}
    action_result = action_result_raw if isinstance(action_result_raw, dict) else {}
    execute_failed = (
        action_result.get("success") is False
        or bool(action_result.get("error"))
        or bool(action_result.get("stderr"))
    )
    # Verdict priority (more-specific first):
    #   1. action_result.success=False — the kubectl call itself failed;
    #      surface the stderr / error string so the operator doesn't see
    #      a generic "escalated without reason" for what was actually an
    #      execute failure (the bug that surfaced in the Phase 14.5a demo).
    #   2. explicit verify.healed True/False
    #   3. mode != active → observe_only
    #   4. escalated → with whatever reason we have
    #   5. unknown
    if healed is True:
        verdict = "healed"
        reason = "Action ran; verify confirmed the cluster recovered."
    elif healed is False:
        verdict = "failed"
        reason = "Action ran but the cluster did NOT recover; agent escalated."
    elif execute_failed and tool:
        verdict = "failed"
        err = action_result.get("error") or action_result.get("stderr") or "kubectl call failed"
        reason = f"`{tool}` action did not succeed — {err}"
    elif mode and mode != "active":
        verdict = "observe_only"
        reason = f"Agent mode is {mode}; action was logged but not executed."
    elif escalated:
        verdict = "escalated"
        # Pick the most-informative reason available.
        esc_reason = incident.get("escalation_reason")
        if isinstance(esc_reason, str) and esc_reason:
            reason = esc_reason
        elif decision.get("reasons"):
            reason = "; ".join(str(r) for r in decision.get("reasons") or [])
        else:
            reason = "Escalated without explicit reason."
    else:
        verdict = "unknown"
        reason = "Incident completed without a terminal classification."
    outcome = SceneOutcome(
        verdict=verdict,
        reason=reason,
        time_to_outcome_seconds=incident.get("duration_seconds")
        if isinstance(incident.get("duration_seconds"), int | float)
        else None,
        confidence=float(diagnosis.get("confidence"))
        if isinstance(diagnosis.get("confidence"), int | float)
        else None,
        rule=str(incident.get("rule")) if incident.get("rule") else None,
        tool=tool,
        policy_decision=decision if decision else None,
    )
    # ---- Resources affected -------------------------------------------
    # The agent's `proposed_action.args` carries the target identity
    # for the action. We synthesize a one-row resource view per target,
    # plus rows for the chaos CR (if the incident came from a use case
    # runner) and the affected pod/node from the incident state.
    resources: list[SceneResource] = []
    if pod and ns:
        # The original target pod — colored by the verdict, which the
        # block above already considered execute_failed. `healed=True`
        # → reconciled; explicit healed=False OR execute_failed →
        # failed; mode!=active → paged; otherwise unchanged.
        if healed is True:
            target_outcome = "reconciled"
        elif healed is False or execute_failed:
            target_outcome = "failed"
        elif escalated:
            target_outcome = "paged"
        else:
            target_outcome = "unchanged"
        # Pod "state" string is operator-readable summary of what the
        # agent observed/did to the pod. Derive from triage severity +
        # action_result, since IncidentState doesn't carry a `phase`
        # field directly. Fall back to severity for the legacy case.
        severity_val = incident.get("severity")
        if execute_failed and tool:
            pod_state = f"({tool} failed)"
        elif healed is True:
            pod_state = "(restarted, healthy)"
        elif healed is False:
            pod_state = "(action ran, still unhealthy)"
        elif escalated:
            pod_state = "(escalated; pod untouched)"
        elif isinstance(severity_val, str):
            pod_state = f"severity={severity_val}"
        else:
            pod_state = "—"
        resources.append(
            SceneResource(
                kind="Pod",
                namespace=ns,
                name=pod,
                state=pod_state,
                outcome=target_outcome,
            )
        )
    # Synthesize the resource the action touched (may overlap with the
    # target pod row — that's fine, the SPA can dedup).
    if tool and proposed.get("args"):
        args = proposed.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        kind_hint = {
            "restart_pod": "Pod",
            "rollout_undo": "Deployment",
            "scale_deployment": "Deployment",
            "set_resources": "Deployment",
            "tune_hpa": "HorizontalPodAutoscaler",
            "evict_workload": "Namespace",
            "cordon_node": "Node",
            "uncordon_node": "Node",
            "drain_node": "Node",
            "page_human": None,
        }.get(tool)
        action_target = (
            args.get("pod_name")
            or args.get("deployment_name")
            or args.get("hpa_name")
            or args.get("node_name")
            or args.get("label_selector")
        )
        if (
            kind_hint
            and action_target
            and not any(r.kind == kind_hint and r.name == action_target for r in resources)
        ):
            resources.append(
                SceneResource(
                    kind=kind_hint,
                    namespace=args.get("namespace")
                    if isinstance(args.get("namespace"), str)
                    else None,
                    name=str(action_target),
                    state="(target of " + tool + ")",
                    outcome=target_outcome
                    if pod and ns
                    else (
                        "reconciled"
                        if healed is True
                        else "paged"
                        if escalated
                        else "failed"
                        if healed is False
                        else "unchanged"
                    ),
                    audit_event_type="execute",
                )
            )
    # ---- Action chain --------------------------------------------------
    # Each major IncidentState transition becomes one step. Order
    # mirrors the graph topology so the SPA renders them as a
    # vertical stepper. Mapping is by SEMANTIC state key
    # (state["diagnosis"], state["proposed_action"], ...) rather than
    # by node name — the graph nodes write to those keys, and that's
    # what `incident_store.save` persists. Triage is special: it
    # doesn't write a per-step dict, so we synthesize one row from
    # the top-level state fields the triage node populates.
    step_order: list[tuple[str, str | None, str]] = [
        ("triage", None, "Event triaged"),
        ("retrieve_context", "context", "Retrieved runbooks + pod state"),
        ("k8sgpt_analyze", "k8sgpt_findings", "Pulled cluster diagnostic signals"),
        ("enrich_with_baselines", "baselines", "Enriched with metric baselines"),
        ("diagnose", "diagnosis", "Diagnosed root cause"),
        ("plan_action", "proposed_action", "Planned remediation"),
        ("policy_gate", "policy_decision", "Policy gate evaluated"),
        ("execute", "action_result", "Action executed"),
        ("verify", "verification", "Verified cluster state"),
    ]
    steps: list[SceneStep] = []
    for step_name, state_key, default_summary in step_order:
        if state_key is None:
            # Triage — synthesize from top-level fields.
            if not incident.get("severity"):
                continue
            steps.append(
                SceneStep(
                    name=step_name,
                    ts=str(incident.get("saved_at") or ""),
                    summary=(
                        f"Triaged: severity={incident.get('severity')} "
                        f"ns={incident.get('namespace') or '?'} "
                        f"pod={incident.get('pod') or '?'}"
                    ),
                    status="ok",
                    payload_keys=["severity", "namespace", "pod", "rule"],
                )
            )
            continue
        raw_section = incident.get(state_key)
        # `k8sgpt_findings` + `baselines` are lists, not dicts — wrap
        # them into a dict so the rest of the projection is uniform.
        if isinstance(raw_section, list):
            section: dict[str, Any] = {"count": len(raw_section)}
        elif isinstance(raw_section, dict):
            section = raw_section
        else:
            # Missing or unexpected shape — skip silently (graph
            # short-circuits leave these out for legitimate reasons).
            continue
        # Status: error overrides warn overrides ok.
        status = "ok"
        if section.get("error"):
            status = "error"
        if step_name == "policy_gate" and section.get("allow") is False:
            status = "warn"
        if step_name == "diagnose" and section.get("confidence", 1.0) < 0.5:
            status = "warn"
        if step_name == "execute" and section.get("success") is False:
            status = "error"
        if step_name == "verify" and section.get("healed") is False:
            status = "warn"
        # One-line operator summary per step.
        if step_name == "diagnose":
            summary = str(section.get("summary") or default_summary)
        elif step_name == "plan_action":
            rationale = section.get("rationale", "")
            summary = f"Chose `{section.get('tool', '?')}` — {rationale}"[:200]
        elif step_name == "policy_gate":
            allow = section.get("allow")
            reasons = section.get("reasons") or []
            joined = ", ".join(str(r) for r in reasons) or "no reasons"
            summary = f"OPA {'allow' if allow else 'deny'} — {joined}"[:200]
        elif step_name == "execute":
            ok = section.get("success")
            summary = f"{'Success' if ok else 'Failed'} — {section.get('tool', '?')}"
        elif step_name == "verify":
            h = section.get("healed")
            summary = "healed" if h is True else "not healed" if h is False else "verify n/a"
        elif step_name == "retrieve_context":
            hits = section.get("runbook_hits") or []
            summary = f"{len(hits)} runbook hit(s)"
            if isinstance(hits, list) and hits and isinstance(hits[0], dict):
                summary += f", top: {hits[0].get('slug', '?')}"
        elif step_name == "k8sgpt_analyze":
            summary = f"{section.get('count', 0)} finding(s)"
        elif step_name == "enrich_with_baselines":
            summary = f"{section.get('count', 0)} baseline snapshot(s)"
        else:
            summary = default_summary
        steps.append(
            SceneStep(
                name=step_name,
                ts=str(incident.get("saved_at") or ""),
                summary=summary,
                status=status,
                payload_keys=sorted(list(section.keys()))[:8],
            )
        )
    return WorkloadScene(
        incident_id=inc_id,
        namespace=ns,
        pod=pod,
        started_at=str(incident.get("started_at") or ""),
        completed_at=str(incident.get("saved_at") or ""),
        outcome=outcome,
        resources=resources,
        steps=steps,
    )


@app.get(
    "/api/scene/{incident_id}",
    response_model=WorkloadScene,
    dependencies=[Depends(require_bearer)],
)
def get_scene(incident_id: str) -> WorkloadScene:
    """Aggregate one incident into the Komodor-style 'scene' payload
    the SPA's use-case-launch view binds to. 404 if the incident
    isn't persisted (yet) — the SPA polls / subscribes via the
    pipeline-trace WS until it lands."""
    from agent import incident_store

    incident = incident_store.load(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id!r} not found")
    return _scene_from_incident(incident)


# ---------------------------------------------------------------------------
# Use cases (CRUD + run)
# ---------------------------------------------------------------------------


@app.get("/api/usecases", response_model=list[UseCase], dependencies=[Depends(require_bearer)])
def list_usecases() -> list[UseCase]:
    # Idempotent: seeds the starter library on first-ever call AND prunes
    # any stale entries (schema-invalid or orphaned starter ids from prior
    # releases). Safe to run on every request; the file write is skipped
    # when nothing changes.
    usecase_store.seed_library_if_empty()
    usecase_store.prune_invalid_and_migrate_library()
    return usecase_store.list_all()


@app.post("/api/usecases", response_model=UseCase, dependencies=[Depends(require_bearer)])
def create_usecase(body: dict[str, Any]) -> UseCase:
    return usecase_store.create(body)


@app.put(
    "/api/usecases/{use_case_id}", response_model=UseCase, dependencies=[Depends(require_bearer)]
)
def update_usecase(use_case_id: str, body: dict[str, Any]) -> UseCase:
    uc = usecase_store.update(use_case_id, body)
    if uc is None:
        raise HTTPException(status_code=404, detail="use case not found")
    return uc


@app.delete("/api/usecases/{use_case_id}", dependencies=[Depends(require_bearer)])
def delete_usecase(use_case_id: str) -> dict[str, bool]:
    ok = usecase_store.delete(use_case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="use case not found")
    return {"deleted": True}


@app.post(
    "/api/usecases/{use_case_id}/run",
    response_model=UseCaseRunResponse,
    dependencies=[Depends(require_bearer)],
)
def run_usecase(use_case_id: str) -> UseCaseRunResponse:
    uc = usecase_store.get(use_case_id)
    if uc is None:
        raise HTTPException(status_code=404, detail="use case not found")
    incident_id, _event = usecase_runner.run(uc)
    return UseCaseRunResponse(incident_id=incident_id, use_case_id=use_case_id)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@app.get(
    "/api/approvals", response_model=list[PendingApproval], dependencies=[Depends(require_bearer)]
)
def list_pending_approvals() -> list[PendingApproval]:
    return approvals.list_pending()


@app.post(
    "/api/actions/approve", response_model=ApprovalResponse, dependencies=[Depends(require_bearer)]
)
def approve_action(body: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse.model_validate(approvals.approve(body.incident_id))


@app.post(
    "/api/actions/deny", response_model=ApprovalResponse, dependencies=[Depends(require_bearer)]
)
def deny_action(body: DenialRequest) -> ApprovalResponse:
    return ApprovalResponse.model_validate(approvals.deny(body.incident_id, body.reason))


# ---------------------------------------------------------------------------
# WebSocket /ws/events
# ---------------------------------------------------------------------------


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket, token: str | None = Query(None)) -> None:
    if not check_ws_token(token):
        await websocket.close(code=4401, reason="invalid bearer token")
        return
    conn = WSConnection(websocket)
    await conn.run()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kubectl_get(kind: str, name: str, namespace: str) -> dict[str, Any]:
    # When running in-cluster the SA token under
    # /var/run/secrets/kubernetes.io/serviceaccount is what kubectl
    # uses automatically (if no kubeconfig is configured). Only pass
    # --kubeconfig when an actual file is present, so the same code
    # path works on a developer laptop (file exists) and in a pod
    # (file absent, in-cluster auth kicks in).
    cmd = ["kubectl"]
    if settings.kubeconfig and os.path.isfile(settings.kubeconfig):
        cmd += ["--kubeconfig", settings.kubeconfig]
    cmd += ["get", kind]
    if name:
        cmd.append(name)
    if namespace:
        cmd += ["-n", namespace]
    cmd += ["-o", "json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return {}
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def _age_seconds(creation_ts: str | None) -> int:
    if not creation_ts:
        return 0
    try:
        dt = datetime.fromisoformat(creation_ts.replace("Z", "+00:00"))
        return int((datetime.now(UTC) - dt).total_seconds())
    except Exception:  # noqa: BLE001
        return 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _verify_record(rec: dict[str, Any], key: bytes) -> bool:
    import hashlib
    import hmac as _hmac

    claimed = rec.get("hmac")
    if not isinstance(claimed, str):
        return False
    body = {k: v for k, v in rec.items() if k != "hmac"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    expected = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, claimed)


# Make the configured token visible at import time so missing env yields a
# deterministic generated value (logged once for the operator).
log.info(
    "API auth token in use: %s",
    "<configured>" if os.environ.get("SHP_API_TOKEN") else "<generated>",
)
_ = get_token()
