"""Action tools the agent's `execute` node can dispatch to, plus the read-only
helpers used during diagnose/retrieve (kubectl describe, embeddings, vector
search).

Each tool is a plain function returning an `ActionResult` (for write tools)
or a typed payload (for read tools). Subprocess errors are captured, never
raised — the graph's escalate path needs structured information, not stack
traces.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

import httpx
from qdrant_client import QdrantClient

from agent.config import kubectl_auth_args, settings
from agent.schemas import ActionResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subprocess helper: every kubectl call goes through here so we can attach
# --kubeconfig consistently and capture stdout/stderr without raising.
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    log.debug("exec: %s", " ".join(cmd))
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout after {timeout}s: {exc}"
    except FileNotFoundError as exc:
        return 127, "", f"executable not found: {exc}"


def _kubectl(args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    return _run(["kubectl", *kubectl_auth_args(), *args], timeout=timeout)


# ---------------------------------------------------------------------------
# Read helpers used by retrieve_context / verify.
# ---------------------------------------------------------------------------


def kubectl_get(kind: str, name: str, namespace: str) -> dict[str, Any]:
    """Return parsed JSON for `kubectl get <kind> <name> -n <ns>`."""
    code, out, err = _kubectl(["get", kind, name, "-n", namespace, "-o", "json"])
    if code != 0:
        return {"error": err.strip() or f"kubectl exit {code}"}
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid json from kubectl: {exc}"}


def kubectl_describe_pod(name: str, namespace: str) -> str:
    code, out, err = _kubectl(["describe", "pod", name, "-n", namespace])
    if code != 0:
        return f"<error: {err.strip()}>"
    # Truncate aggressively — descriptions can be huge and we only need the
    # status/events tail for the LLM context.
    if len(out) > 4000:
        out = out[:4000] + "\n... <truncated>"
    return out


# ---------------------------------------------------------------------------
# Embeddings + vector search.
# ---------------------------------------------------------------------------


# Retry policy shared by embed + vector search. Same shape as the LLM
# retry (3 attempts, 1/2/4s backoff) so operators only have one mental model.
_QDRANT_RETRY_ATTEMPTS = 3
_QDRANT_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)


def embed_text(text: str) -> list[float]:
    """Call Ollama's embeddings endpoint with bounded retries.

    Returns the embedding vector, or raises the LAST httpx error after all
    retries are exhausted. Callers (graph nodes) typically catch and route
    to escalate."""
    import time as _time

    body = {"model": settings.embed_model, "prompt": text}
    last_err: Exception | None = None
    for attempt in range(_QDRANT_RETRY_ATTEMPTS):
        try:
            resp = httpx.post(f"{settings.ollama_host}/api/embeddings", json=body, timeout=60.0)
            resp.raise_for_status()
            return resp.json()["embedding"]
        except httpx.RequestError as exc:
            last_err = exc
            log.warning(
                "embed_text request failed (%s) (attempt %d/%d); retrying in %.1fs",
                type(exc).__name__,
                attempt + 1,
                _QDRANT_RETRY_ATTEMPTS,
                _QDRANT_RETRY_BACKOFF_S[attempt],
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                # 4xx is a real bug (bad payload, wrong model name) — don't retry.
                raise
            last_err = exc
            log.warning(
                "embed_text %s (attempt %d/%d); retrying",
                exc.response.status_code,
                attempt + 1,
                _QDRANT_RETRY_ATTEMPTS,
            )
        if attempt + 1 < _QDRANT_RETRY_ATTEMPTS:
            _time.sleep(_QDRANT_RETRY_BACKOFF_S[attempt])
    raise RuntimeError(f"embed_text failed after {_QDRANT_RETRY_ATTEMPTS} attempts: {last_err}")


def vector_search(vector: list[float], top_k: int = 3) -> list[dict[str, Any]]:
    """Search the runbooks collection with bounded retries.

    On any failure after all retries, returns `[]` (graceful degrade). The
    graph still runs, just without runbook context — diagnose will produce a
    lower-confidence answer that's more likely to escalate, which is the
    correct safety behavior. Better than crashing the graph.

    Returns [{slug, text, score}, ...].
    """
    import time as _time

    last_err: Exception | None = None
    for attempt in range(_QDRANT_RETRY_ATTEMPTS):
        try:
            client = QdrantClient(url=settings.qdrant_url, timeout=10.0)
            hits = client.query_points(
                collection_name=settings.qdrant_collection,
                query=vector,
                limit=top_k,
                with_payload=True,
            ).points
            return [
                {
                    "slug": h.payload.get("slug", "unknown") if h.payload else "unknown",
                    "text": h.payload.get("text", "") if h.payload else "",
                    "score": float(h.score),
                }
                for h in hits
            ]
        except Exception as exc:  # noqa: BLE001 — Qdrant client raises many types
            last_err = exc
            log.warning(
                "vector_search failed (%s) (attempt %d/%d)",
                type(exc).__name__,
                attempt + 1,
                _QDRANT_RETRY_ATTEMPTS,
            )
            if attempt + 1 < _QDRANT_RETRY_ATTEMPTS:
                _time.sleep(_QDRANT_RETRY_BACKOFF_S[attempt])
    # All attempts failed → graceful degrade. Audit + log it loudly so an
    # alerting rule can fire on the Loki side.
    log.error("vector_search exhausted retries; returning empty hits. last_err=%s", last_err)
    return []


# ---------------------------------------------------------------------------
# Write tools — every one of these may be gated to a dry-run by the caller.
# ---------------------------------------------------------------------------


def restart_pod(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    args = {"pod_name": pod_name, "namespace": namespace}
    if dry_run:
        return ActionResult(
            tool="restart_pod",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl delete pod {pod_name} -n {namespace}",
        )
    code, out, err = _kubectl(["delete", "pod", pod_name, "-n", namespace])
    return ActionResult(
        tool="restart_pod",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def rollout_undo(deployment_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    args = {"deployment_name": deployment_name, "namespace": namespace}
    if dry_run:
        return ActionResult(
            tool="rollout_undo",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl rollout undo deployment/{deployment_name} -n {namespace}",
        )
    code, out, err = _kubectl(["rollout", "undo", f"deployment/{deployment_name}", "-n", namespace])
    return ActionResult(
        tool="rollout_undo",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def scale_deployment(
    deployment_name: str, namespace: str, replicas: int, dry_run: bool = False
) -> ActionResult:
    """Set a Deployment's replica count. Reversible: the operator (or the
    HPA, if one's attached) can re-tune. Used for traffic surges, capacity
    headroom, and clearing transient resource-imbalance hot spots."""
    args = {"deployment_name": deployment_name, "namespace": namespace, "replicas": replicas}
    if dry_run:
        return ActionResult(
            tool="scale_deployment",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl scale deployment/{deployment_name} --replicas={replicas} -n {namespace}",
        )
    code, out, err = _kubectl(
        [
            "scale",
            f"deployment/{deployment_name}",
            f"--replicas={replicas}",
            "-n",
            namespace,
        ]
    )
    return ActionResult(
        tool="scale_deployment",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def tune_hpa(
    hpa_name: str,
    namespace: str,
    min_replicas: int | None = None,
    max_replicas: int | None = None,
    target_cpu: int | None = None,
    dry_run: bool = False,
) -> ActionResult:
    """Phase 14 — adjust a HorizontalPodAutoscaler's bounds + target.

    Used by the rightsizing + traffic-forecast paths: when the
    forecaster predicts a load ramp at t+5 min, the agent can widen
    the HPA's max BEFORE the reactive scaling threshold trips,
    avoiding the "stampede" failure mode (HPA scales too slowly →
    pods OOM → restarts → cascading failure).

    All three knobs are optional so the agent can adjust just one
    axis without disturbing the others. At least one MUST be set;
    the function returns a failed ActionResult otherwise."""
    args = {
        "hpa_name": hpa_name,
        "namespace": namespace,
        "min_replicas": min_replicas,
        "max_replicas": max_replicas,
        "target_cpu": target_cpu,
    }
    if min_replicas is None and max_replicas is None and target_cpu is None:
        return ActionResult(
            tool="tune_hpa",
            args=args,
            dry_run=dry_run,
            success=False,
            error="tune_hpa needs at least one of min_replicas / max_replicas / target_cpu",
        )
    # Build the JSON Merge Patch. `spec.minReplicas` + `spec.maxReplicas`
    # are top-level; CPU target lives under metrics[] (autoscaling/v2).
    # We use the simpler `spec.targetCPUUtilizationPercentage` path
    # (autoscaling/v1 compatibility) — k3d ships v2 controllers that
    # honour both. Operators on v2-only setups can re-target via the
    # native metrics[] block by extending this function.
    patch: dict[str, Any] = {"spec": {}}
    if min_replicas is not None:
        patch["spec"]["minReplicas"] = int(min_replicas)
    if max_replicas is not None:
        patch["spec"]["maxReplicas"] = int(max_replicas)
    if target_cpu is not None:
        patch["spec"]["targetCPUUtilizationPercentage"] = int(target_cpu)
    if dry_run:
        return ActionResult(
            tool="tune_hpa",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl patch hpa/{hpa_name} -n {namespace} --type=merge -p {patch!r}",
        )
    import json as _json

    code, out, err = _kubectl(
        [
            "patch",
            f"hpa/{hpa_name}",
            "-n",
            namespace,
            "--type=merge",
            "-p",
            _json.dumps(patch),
        ]
    )
    return ActionResult(
        tool="tune_hpa",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def set_resources(
    deployment_name: str,
    namespace: str,
    container: str,
    requests: dict[str, str] | None = None,
    limits: dict[str, str] | None = None,
    dry_run: bool = False,
) -> ActionResult:
    """Phase 14 — patch a Deployment container's resource requests/limits.

    Driven by the forecasted-OOM path (raise memory limits before the
    kubelet evicts) and the rightsizing path (cut over-provisioned
    requests reclaimed during baseline analysis). The agent only ever
    moves limits UP (avoid pulling the rug from a hot pod) and moves
    requests DOWN (free up scheduler capacity). The runbook decides
    direction; this function just applies the patch.

    `requests` / `limits` are dicts like `{"memory": "512Mi"}` or
    `{"cpu": "200m", "memory": "1Gi"}`. K8s resource-quantity syntax
    is enforced by the API server, not by this function."""
    args = {
        "deployment_name": deployment_name,
        "namespace": namespace,
        "container": container,
        "requests": requests,
        "limits": limits,
    }
    if not requests and not limits:
        return ActionResult(
            tool="set_resources",
            args=args,
            dry_run=dry_run,
            success=False,
            error="set_resources needs at least one of requests / limits",
        )
    resources: dict[str, Any] = {}
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits
    # Strategic-merge patch on the Deployment's pod template. Container
    # match-by-name preserves the rest of the pod spec (env, volumeMounts,
    # securityContext, …) — anything we don't mention is untouched.
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": container, "resources": resources},
                    ]
                }
            }
        }
    }
    if dry_run:
        return ActionResult(
            tool="set_resources",
            args=args,
            dry_run=True,
            success=True,
            stdout=(
                f"(dry-run) would: kubectl patch deploy/{deployment_name} -n {namespace} "
                f"-p {patch!r}"
            ),
        )
    import json as _json

    code, out, err = _kubectl(
        [
            "patch",
            f"deployment/{deployment_name}",
            "-n",
            namespace,
            "--type=strategic",
            "-p",
            _json.dumps(patch),
        ]
    )
    return ActionResult(
        tool="set_resources",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def evict_workload(
    namespace: str,
    label_selector: str,
    dry_run: bool = False,
) -> ActionResult:
    """Phase 14 — delete every pod matching a label selector.

    The scheduler reschedules them, which is the intended "soft
    restart" for a hot pod or noisy-neighbour scenario. NOT a node
    drain — pods land back on the same node unless the operator has
    set anti-affinity, cordoned the node, or paired this call with
    a separate cordon_node. The label-selector contract keeps this
    blast-radius-bounded: an empty selector is rejected so the
    agent can't accidentally evict every pod in a namespace.

    Reversible only in the loosest sense — the new pods are
    different identities. Use for stateless workloads; the policy
    matrix gates this aggressively at low severity."""
    args = {"namespace": namespace, "label_selector": label_selector}
    if not label_selector or not label_selector.strip():
        return ActionResult(
            tool="evict_workload",
            args=args,
            dry_run=dry_run,
            success=False,
            error="evict_workload requires a non-empty label_selector",
        )
    if dry_run:
        return ActionResult(
            tool="evict_workload",
            args=args,
            dry_run=True,
            success=True,
            stdout=(f"(dry-run) would: kubectl delete pods -n {namespace} -l {label_selector}"),
        )
    code, out, err = _kubectl(["delete", "pods", "-n", namespace, "-l", label_selector])
    return ActionResult(
        tool="evict_workload",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def revert_drift(
    kind: str,
    namespace: str | None,
    name: str,
    sot_source: str,
    dry_run: bool = False,
) -> ActionResult:
    """Phase 15 — re-apply the source-of-truth manifest to the cluster.

    `sot_source` is a pointer into the SoT provider's storage —
    typically a filesystem path like `manifests/demo-workloads.yaml#1`.
    The format must round-trip with `agent.drift.sources.ManifestsSource`,
    which is the v1 provider; future ArgoCD/Flux providers will land
    their own resolver here.

    Reversible in the loosest sense — applying the SoT manifest
    overwrites the drifted live state. The drift event payload
    carries the deltas the diff produced, so an operator who wants
    to see what's about to change can read it from the audit chain
    before approving.

    On a single-document file, `sot_source` is just the path.
    On a multi-doc file we encode the doc index as `path#N`.
    """
    args = {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "sot_source": sot_source,
    }
    # Parse path[#N] back to (path, doc_index).
    if "#" in sot_source:
        raw_path, raw_idx = sot_source.rsplit("#", 1)
        try:
            doc_index = int(raw_idx)
        except ValueError:
            return ActionResult(
                tool="revert_drift",
                args=args,
                dry_run=dry_run,
                success=False,
                error=f"sot_source has non-integer doc index: {sot_source!r}",
            )
    else:
        raw_path, doc_index = sot_source, 0
    if not raw_path:
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=dry_run,
            success=False,
            error="revert_drift needs a non-empty sot_source",
        )
    # When asked to dry-run we don't even read the file (the agent
    # may be running with no SoT mount); just synthesise the
    # planned kubectl command.
    if dry_run:
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl apply -f {raw_path} (doc {doc_index})",
        )
    # Read the manifest document. We extract just the target
    # document to avoid re-applying every doc in the file (which
    # could blast other drifted resources unexpectedly).
    try:
        import yaml as _yaml

        with open(raw_path, encoding="utf-8") as fh:
            docs = list(_yaml.safe_load_all(fh))
    except FileNotFoundError:
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=False,
            success=False,
            error=f"sot_source not found: {raw_path!r}",
        )
    except _yaml.YAMLError as exc:
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=False,
            success=False,
            error=f"cannot parse {raw_path!r}: {exc}",
        )
    if doc_index >= len(docs):
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=False,
            success=False,
            error=f"doc index {doc_index} out of range for {raw_path!r}",
        )
    doc = docs[doc_index]
    if not isinstance(doc, dict):
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=False,
            success=False,
            error=f"{raw_path}#{doc_index} is not a mapping",
        )
    # Defensive guard: refuse to apply the wrong kind/name/ns. The
    # drift detector populates these from the same file we just read,
    # but a stale audit-driven re-run might point at a different
    # manifest that's been swapped on disk.
    if doc.get("kind") != kind or (doc.get("metadata") or {}).get("name") != name:
        return ActionResult(
            tool="revert_drift",
            args=args,
            dry_run=False,
            success=False,
            error=(
                f"sot_source {raw_path}#{doc_index} does not match requested target {kind}/{name}"
            ),
        )
    # Write the single doc to a temp file kubectl can read. Tempdir
    # cleanup is automatic at process exit; we use named-tmp so
    # `kubectl apply -f <path>` works on every platform.
    import json as _json
    import tempfile

    fd, tmp_path = tempfile.mkstemp(prefix="keos-revert-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _json.dump(doc, fh)
        code, out, err = _kubectl(["apply", "-f", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return ActionResult(
        tool="revert_drift",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def cordon_node(node_name: str, dry_run: bool = False) -> ActionResult:
    """Mark a node unschedulable. Reversible via `kubectl uncordon`. Does
    not evict existing pods — pair with drain_node when full evacuation is
    needed. Used for nodes showing MemoryPressure / DiskPressure / PID
    pressure before they cause cascading evictions."""
    args = {"node_name": node_name}
    if dry_run:
        return ActionResult(
            tool="cordon_node",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl cordon {node_name}",
        )
    code, out, err = _kubectl(["cordon", node_name])
    return ActionResult(
        tool="cordon_node",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def drain_node(node_name: str, grace_period_s: int = 60, dry_run: bool = False) -> ActionResult:
    """Cordon + evict all evictable pods from a node. The scheduler then
    places those pods on healthier nodes. Higher-blast-radius than
    cordon_node alone; the OPA matrix only allows this at high severity.
    Reversible via `kubectl uncordon` once the node is fixed."""
    args = {"node_name": node_name, "grace_period_s": grace_period_s}
    if dry_run:
        return ActionResult(
            tool="drain_node",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data --grace-period={grace_period_s}",
        )
    code, out, err = _kubectl(
        [
            "drain",
            node_name,
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            f"--grace-period={grace_period_s}",
        ],
        timeout=120.0,
    )
    return ActionResult(
        tool="drain_node",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def uncordon_node(node_name: str, dry_run: bool = False) -> ActionResult:
    """Re-enable scheduling on a previously cordoned node. Pair with
    cordon_node/drain_node when a node is healthy again — without this,
    the cluster stays in a degraded state where new pods can't schedule.
    This is the natural remediation for `FailedScheduling: nodes are
    unschedulable`."""
    args = {"node_name": node_name}
    if dry_run:
        return ActionResult(
            tool="uncordon_node",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: kubectl uncordon {node_name}",
        )
    code, out, err = _kubectl(["uncordon", node_name])
    return ActionResult(
        tool="uncordon_node",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=None if code == 0 else f"kubectl exit {code}",
    )


def page_human(
    reason: str,
    dry_run: bool = False,
    incident_id: str | None = None,
    namespace: str | None = None,
    pod: str | None = None,
    proposed_tool: str | None = None,
    severity: str | None = None,
) -> ActionResult:
    """Notify a human. Three transports, all best-effort:

      1. Structured log line (always; the audit record is the canonical receipt).
      2. Slack webhook (if `SLACK_WEBHOOK_URL` is set in env).
      3. Generic HTTP webhook (if `PAGE_HUMAN_WEBHOOK_URL` is set in env).

    Webhook failures are logged but never raise — paging a human must
    never block the graph from completing the escalate node. Audit log
    always records the page attempt, whether the webhook succeeded or not.
    """
    args = {
        "reason": reason,
        "incident_id": incident_id,
        "namespace": namespace,
        "pod": pod,
        "proposed_tool": proposed_tool,
        "severity": severity,
    }
    log.warning(
        "PAGE_HUMAN: %s (incident=%s ns=%s pod=%s tool=%s sev=%s)",
        reason,
        incident_id,
        namespace,
        pod,
        proposed_tool,
        severity,
    )

    delivery: list[dict[str, Any]] = []
    if not dry_run:
        delivery = _send_notifications(args)

    return ActionResult(
        tool="page_human",
        args=args,
        dry_run=dry_run,
        success=True,
        stdout=f"paged human (dry_run={dry_run}): {reason}",
        # `meta` lets callers + the audit log see which channels actually got the page.
        meta={"delivery": delivery} if delivery else None,
    )


def _send_notifications(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort fan-out to configured channels. Returns one entry per
    attempted channel with `{channel, ok, status, error?}` so the audit
    record shows exactly what happened."""
    import os

    out: list[dict[str, Any]] = []

    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if slack_url:
        out.append(_post_slack(slack_url, args))

    generic_url = os.environ.get("PAGE_HUMAN_WEBHOOK_URL", "").strip()
    if generic_url:
        out.append(_post_generic(generic_url, args))

    return out


def _post_slack(url: str, args: dict[str, Any]) -> dict[str, Any]:
    """Format an incident notification as a Slack message and POST it.
    Uses Block Kit for a richer card; falls back to a plain `text` field
    on older webhooks. 5 s timeout."""
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        return {"channel": "slack", "ok": False, "error": f"httpx missing: {exc}"}

    text = f":rotating_light: *Keos SRE Agent escalation*\n{args.get('reason', '')}"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Keos SRE Agent — escalation"}},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reason:* {args.get('reason', '(no reason)')}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident:*\n`{args.get('incident_id') or '—'}`"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{args.get('severity') or '—'}"},
                {"type": "mrkdwn", "text": f"*Namespace:*\n`{args.get('namespace') or '—'}`"},
                {"type": "mrkdwn", "text": f"*Pod / Target:*\n`{args.get('pod') or '—'}`"},
                {
                    "type": "mrkdwn",
                    "text": f"*Proposed action:*\n`{args.get('proposed_tool') or '—'}`",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Action denied by OPA policy gate. Operator approval required to proceed.",
                },
            ],
        },
    ]
    body = {"text": text, "blocks": blocks}

    try:
        r = httpx.post(url, json=body, timeout=5.0)
        ok = 200 <= r.status_code < 300
        result: dict[str, Any] = {"channel": "slack", "ok": ok, "status": r.status_code}
        if not ok:
            result["error"] = (r.text or "")[:200]
        return result
    except Exception as exc:  # noqa: BLE001
        return {"channel": "slack", "ok": False, "error": str(exc)[:200]}


def _post_generic(url: str, args: dict[str, Any]) -> dict[str, Any]:
    """Plain JSON POST for anything that isn't Slack (PagerDuty events
    API, custom webhooks, Microsoft Teams, etc.). 5 s timeout."""
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        return {"channel": "webhook", "ok": False, "error": f"httpx missing: {exc}"}
    try:
        r = httpx.post(url, json=args, timeout=5.0)
        ok = 200 <= r.status_code < 300
        return {"channel": "webhook", "ok": ok, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "webhook", "ok": False, "error": str(exc)[:200]}


# ---------------------------------------------------------------------------
# Verification: poll the cluster for a healed condition.
# ---------------------------------------------------------------------------


def verify_pod_recovered(
    namespace: str, pod_name_hint: str | None, deployment_name: str | None, timeout_s: float = 30.0
) -> dict[str, Any]:
    """For restart_pod: confirm Deployment has full replicas Ready.
    For rollout_undo: same.
    For pod-only actions where deployment isn't known: poll until at least one
    pod with the same app prefix is Ready, OR timeout.
    """
    import time

    deadline = time.monotonic() + timeout_s
    checks: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        # Prefer deployment readiness when known.
        target = deployment_name
        if target:
            info = kubectl_get("deployment", target, namespace)
            status = info.get("status", {}) if "error" not in info else {}
            ready = int(status.get("readyReplicas", 0) or 0)
            desired = int(status.get("replicas", 0) or 0)
            checks.append(
                {"kind": "deployment", "name": target, "ready": ready, "desired": desired}
            )
            if desired > 0 and ready >= desired:
                return {
                    "healed": True,
                    "checks": checks,
                    "notes": f"deployment/{target} fully ready ({ready}/{desired})",
                }
        else:
            # Fall back to listing pods by name prefix.
            code, out, _ = _kubectl(["get", "pods", "-n", namespace, "-o", "json"])
            if code == 0:
                pods = json.loads(out).get("items", [])
                matching = [
                    p
                    for p in pods
                    if pod_name_hint
                    and p["metadata"]["name"].startswith(pod_name_hint.split("-")[0])
                ]
                ready_pods = [p for p in matching if _pod_ready(p)]
                checks.append({"kind": "pods", "matched": len(matching), "ready": len(ready_pods)})
                if matching and ready_pods:
                    return {
                        "healed": True,
                        "checks": checks,
                        "notes": f"{len(ready_pods)}/{len(matching)} pods ready",
                    }
        time.sleep(2)
    return {
        "healed": False,
        "checks": checks,
        "notes": f"timeout after {timeout_s}s waiting for recovery",
    }


def _pod_ready(pod: dict[str, Any]) -> bool:
    conds = pod.get("status", {}).get("conditions", [])
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds)
