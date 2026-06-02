"""LangGraph state machine for the self-healing agent.

Topology (text):

    triage -> retrieve_context -> diagnose ─(conf<thr)─> escalate -> END
                                     │
                                     v
                                plan_action -> policy_gate ─(deny)─> escalate -> END
                                                  │
                                                  v (allow)
                                              execute -> verify ─(unhealed)─> escalate -> END
                                                            │
                                                            v (healed)
                                                          END

Two entrypoints:
  python -m agent.graph --once --scenario N
      Builds one synthetic event in-process, runs the graph end-to-end,
      prints the final state.
  python -m agent.graph --serve
      Subscribes to NATS subject `infra.events.>` (JetStream durable
      consumer) and runs the graph for each message.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import UTC
from typing import Any

from langgraph.graph import END, StateGraph

from agent import tools
from agent.audit import AuditLogger
from agent.config import settings
from agent.k8sgpt_client import analyze_sync as k8sgpt_analyze_sync
from agent.llm import diagnose as llm_diagnose
from agent.llm import plan_action as llm_plan_action
from agent.llm import warmup_chat_model
from agent.logging_utils import configure_logging
from agent.mode_store import mode_store
from agent.policy_gate import evaluate as opa_evaluate
from agent.schemas import (
    IncidentState,
    PolicyDecision,
    ProposedAction,
    Severity,
    Verification,
)

log = logging.getLogger("graph")
audit = AuditLogger()


# ---------------------------------------------------------------------------
# Falco-priority -> severity map. Falco emits string priorities like
# "Warning" or "Critical"; we normalise to the matrix's four buckets.
# ---------------------------------------------------------------------------

_FALCO_PRIORITY_TO_SEVERITY: dict[str, Severity] = {
    "Emergency": "critical",
    "Alert": "critical",
    "Critical": "critical",
    "Error": "high",
    "Warning": "medium",
    "Notice": "low",
    "Informational": "low",
    "Info": "low",
    "Debug": "low",
}


# ===========================================================================
# Nodes — each is a pure function (state) -> partial state update.
# Each node ALSO emits one audit record on entry/exit.
# ===========================================================================


def node_triage(state: IncidentState) -> dict[str, Any]:
    event = state["raw_event"]
    fields = event.get("output_fields", {}) or {}
    severity = _FALCO_PRIORITY_TO_SEVERITY.get(event.get("priority", "Warning"), "medium")
    namespace = fields.get("k8s.ns.name") or "workloads"
    pod = fields.get("k8s.pod.name") or ""
    container = fields.get("container.name")
    rule = event.get("rule", "<unknown rule>")
    # Snapshot the current mode RIGHT NOW so every incident (including ones
    # that short-circuit at triage) carries the correct agent_mode for the
    # UI. Without this, short-circuited incidents render "None" in the
    # mode column even when the cluster is in active mode.
    try:
        current_mode = mode_store.get()
    except Exception:  # noqa: BLE001
        current_mode = None
    out = {
        "severity": severity,
        "namespace": namespace,
        "pod": pod,
        "container": container,
        "rule": rule,
        "agent_mode": current_mode,
    }

    # Staleness check. On CPU-only inference the graph takes 3-5 minutes
    # per event, and JetStream's retention keeps unprocessed messages
    # buffered. By the time we reach this node, the underlying problem
    # may have already healed itself (probe came back, scheduler caught
    # up, kubelet restarted the container). Acting on a stale event
    # produces misleading "Escalated" incidents and wastes a full
    # LLM cycle.
    #
    # The check: if the event fired more than `STALE_EVENT_S` seconds ago
    # AND the targeted pod is currently Ready in the cluster, mark the
    # state with `stale_resolved=True` and let the conditional edge route
    # straight to escalate with a clear "already healed" reason.
    import os

    stale_s = int(os.environ.get("STALE_EVENT_S", "600") or 600)
    is_stale = False
    if stale_s > 0:
        try:
            from datetime import datetime

            evt_time = fields.get("evt.time") or event.get("time")
            if evt_time:
                # Parse `…Z` and offset-aware ISO timestamps alike.
                ts = datetime.fromisoformat(str(evt_time).replace("Z", "+00:00"))
                age = (datetime.now(UTC) - ts).total_seconds()
                if age > stale_s:
                    # Short-circuit if the pod looks healthy right now OR
                    # the pod no longer exists (both = nothing to act on).
                    if pod and namespace:
                        resolved, why = _is_pod_state_resolved(namespace, pod)
                        if resolved:
                            is_stale = True
                            out["stale_resolved"] = True
                            out["stale_reason"] = why
                            out["staleness_age_s"] = int(age)
                            out["escalation_reason"] = (
                                f"event is {int(age)}s old and pod {pod} is "
                                f"{'already Ready' if why == 'already_ready' else 'gone from cluster'}; "
                                f"skipping LLM cycle"
                            )
                            log.info(
                                "triage: skipping stale event for %s/%s (age=%ds, why=%s)",
                                namespace,
                                pod,
                                age,
                                why,
                            )
        except Exception as exc:  # noqa: BLE001 — staleness is best-effort
            log.debug("triage: staleness check failed (%s); processing anyway", exc)

    audit.emit(
        state["incident_id"], "triage", {"input": event, "result": out, "stale_resolved": is_stale}
    )
    return out


def _is_pod_state_resolved(namespace: str, pod_name: str) -> tuple[bool, str]:
    """Return (resolved, why). The event is considered already-resolved if:

      - The pod is Ready=True in the cluster right now (`already_ready`), OR
      - The pod doesn't exist at all (`pod_gone`).

    Both states mean "the original signal no longer reflects reality":

      - already_ready  → the kubelet event was transient; the probe/restart
                         healed itself before we got around to processing.
      - pod_gone       → the ReplicaSet rolled, an earlier action deleted
                         the pod, or it was evicted. Acting on a NotFound
                         resource would 404 anyway.

    Returns (False, "") on any error so unrelated infrastructure problems
    (kubectl timeout, RBAC, …) don't cause us to silently skip real events.
    """
    try:
        import subprocess

        from agent.config import settings as _s

        p = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                _s.kubeconfig,
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode == 0:
            if (p.stdout or "").strip() == "True":
                return True, "already_ready"
            return False, ""
        # rc != 0 — distinguish "not found" (resolved) from other errors.
        stderr = (p.stderr or "").lower()
        if "notfound" in stderr or "not found" in stderr:
            return True, "pod_gone"
        return False, ""
    except Exception:  # noqa: BLE001
        return False, ""


def node_retrieve_context(state: IncidentState) -> dict[str, Any]:
    event = state["raw_event"]
    query = f"{state.get('rule', '')} :: {event.get('output', '')}"
    try:
        vec = tools.embed_text(query)
        hits = tools.vector_search(vec, top_k=3)
    except Exception as exc:  # noqa: BLE001
        log.exception("retrieve_context failed")
        hits = []
        return {
            "context": {"runbook_hits": hits, "pod_describe": "", "error": str(exc)},
        }
    describe = ""
    if state.get("pod") and state.get("namespace"):
        describe = tools.kubectl_describe_pod(state["pod"], state["namespace"])
    ctx = {
        "runbook_hits": hits,
        "pod_describe": describe,
        "top_slug": hits[0]["slug"] if hits else None,
    }
    audit.emit(
        state["incident_id"],
        "retrieve_context",
        {"num_hits": len(hits), "top_slug": ctx["top_slug"]},
    )
    return {"context": ctx}


def node_k8sgpt_analyze(state: IncidentState) -> dict[str, Any]:
    """Enrich the incident with structured findings from K8sGPT.

    Reads `state["namespace"]`; queries the `Pod` analyzer (and `Deployment`
    if we know a parent). Filters returned findings to those that touch the
    incident's target pod or its owner. On any failure: log + return empty
    findings — the graph continues. Fail-OPEN-but-empty is intentional:
    diagnose still runs, just without the k8sgpt source.
    """
    ns = state.get("namespace") or "workloads"
    pod = state.get("pod") or ""
    filters = ["Pod", "Deployment"]
    try:
        all_findings = k8sgpt_analyze_sync(ns, filters)
    except Exception as exc:  # noqa: BLE001 — never let k8sgpt block the graph
        log.warning("k8sgpt_analyze raised %s; continuing with empty findings", exc)
        all_findings = []
    # Filter to findings that mention the target pod by name or via its owner.
    targeted: list[dict[str, Any]] = []
    pod_short = pod.split("/")[-1] if pod else ""
    for f in all_findings:
        name = f.name or ""
        parent = f.parent_object or ""
        if not pod_short:
            targeted.append(f.model_dump())
            continue
        # k8sgpt names look like "workloads/nginx-demo-7b8d5..."; match the pod portion.
        if (
            pod_short in name
            or pod_short.rsplit("-", 2)[0] in name
            or pod_short.rsplit("-", 2)[0] in parent
        ):
            targeted.append(f.model_dump())
    audit.emit(
        state["incident_id"],
        "k8sgpt_analyze",
        {
            "namespace": ns,
            "raw_count": len(all_findings),
            "matched_count": len(targeted),
            "findings": targeted,
        },
    )
    return {"k8sgpt_findings": targeted}


def node_enrich_with_baselines(state: IncidentState) -> dict[str, Any]:
    """Phase 13 — attach per-pod metric baselines for the diagnose LLM.

    Reads `state["namespace"]` + `state["pod"]`, asks the baselines
    store for the top-N most-interesting snapshots, returns them under
    `baselines`. Wide try/except: a baseline store failure must NEVER
    block the graph. Fail-OPEN-but-empty — diagnose still runs without
    the snapshots, same pattern as `k8sgpt_analyze`.
    """
    ns = state.get("namespace") or ""
    pod = state.get("pod") or ""
    snapshots: list[dict[str, Any]] = []
    if ns and pod:
        try:
            from agent.baselines import snapshot_for_pod

            snapshots = [s.to_dict() for s in snapshot_for_pod(ns, pod)]
        except Exception as exc:  # noqa: BLE001
            log.warning("enrich_with_baselines raised %s; continuing empty", exc)
            snapshots = []
    # Project the σ-above-baseline distribution into Prometheus so
    # dashboards can answer "did the baselines actually help?". Best-
    # effort; never break the graph on a metrics push failure.
    try:
        from agent.metrics import baseline_enrich_sigma_above

        for snap in snapshots:
            sigma = snap.get("sigma_above")
            if isinstance(sigma, int | float) and sigma > 0:
                baseline_enrich_sigma_above.observe(float(sigma))
    except Exception as exc:  # noqa: BLE001
        log.debug("baseline metric emit failed (%s); continuing", exc)
    audit.emit(
        state["incident_id"],
        "enrich_with_baselines",
        {
            "namespace": ns,
            "pod": pod,
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        },
    )
    return {"baselines": snapshots}


def node_diagnose(state: IncidentState) -> dict[str, Any]:
    ctx = state.get("context", {}) or {}
    d = llm_diagnose(
        state["raw_event"],
        ctx.get("runbook_hits", []),
        ctx.get("pod_describe", ""),
        state.get("k8sgpt_findings", []),
        state.get("baselines", []),
    )
    if d is None:
        audit.emit(state["incident_id"], "diagnose_failed", {"reason": "validation"})
        return {
            "diagnosis": {
                "summary": "validation failed twice",
                "root_cause": "unknown",
                "confidence": 0.0,
                "evidence": [],
            }
        }
    payload = d.model_dump()
    audit.emit(state["incident_id"], "diagnose", payload)

    # Phase 12 — record the confidence sample for per-rule calibration.
    # Best-effort; never lets a calibration error break the graph.
    try:
        from agent.calibration import record_diagnose
        from agent.metrics import diagnose_confidence

        rule = state.get("rule")
        conf = float(payload.get("confidence", 0.0))
        if rule:
            record_diagnose(rule, conf)
            diagnose_confidence.labels(rule=rule).observe(conf)
    except Exception as exc:  # noqa: BLE001
        log.debug("calibration: diagnose-record failed (%s); graph continues", exc)

    return {"diagnosis": payload}


def node_plan_action(state: IncidentState) -> dict[str, Any]:
    p = llm_plan_action(
        state["raw_event"], state.get("diagnosis", {}), state.get("pod"), state.get("namespace")
    )
    if p is None:
        audit.emit(state["incident_id"], "plan_action_failed", {"reason": "validation"})
        # Fall back to page_human so the gate still has a real action shape.
        fallback = ProposedAction(
            tool="page_human",
            args={"reason": "plan_action LLM produced no valid response"},
            scope="n/a",
            reversible=True,
            rationale="auto-fallback after two failed plan_action attempts",
        )
        return {"proposed_action": fallback.model_dump()}
    payload = p.model_dump()
    audit.emit(state["incident_id"], "plan_action", payload)
    return {"proposed_action": payload}


def node_policy_gate(state: IncidentState) -> dict[str, Any]:
    action = ProposedAction.model_validate(state["proposed_action"])
    mode = mode_store.get()
    decision = opa_evaluate(action, state["severity"], mode, state.get("namespace"))
    out = decision.model_dump() | {"evaluated_with_mode": mode}
    audit.emit(state["incident_id"], "policy_gate", out)
    return {"policy_decision": out, "agent_mode": mode}


def node_execute(state: IncidentState) -> dict[str, Any]:
    action = ProposedAction.model_validate(state["proposed_action"])
    mode = state.get("agent_mode", mode_store.get())
    dry_run = mode != "active"
    # Two execution paths:
    #   * MCP path (default): the agent calls the MCP server over HTTP. This
    #     exercises the same code path a human operator would use via Claude
    #     Desktop, with the MCP server's own OPA check as defense-in-depth.
    #   * Direct path (MCP_BYPASS=1): call agent.tools.* in-process. Used by
    #     the hermetic test suite and as a fallback if the MCP server is down.
    if dry_run or os.environ.get("MCP_BYPASS"):
        result = _dispatch(action, dry_run)
    else:
        result = _execute_via_mcp(action)
    payload = result.model_dump()
    audit.emit(state["incident_id"], "execute", payload | {"mode": mode, "dry_run": dry_run})
    return {"action_result": payload}


def _execute_via_mcp(action: ProposedAction):
    """Dispatch through the local MCP server. Falls back to direct dispatch
    on connection error so a momentarily-down MCP server doesn't break the
    graph entirely."""
    import asyncio

    from fastmcp import Client

    from agent.schemas import ActionResult

    url = os.environ.get("MCP_URL", "http://localhost:8090/mcp")
    args = dict(action.args or {})
    # MCP tool signatures use 'pod' and 'deployment' (not pod_name/deployment_name).
    mcp_args = _action_to_mcp_args(action, args)

    async def _run() -> ActionResult:
        async with Client(url) as c:
            res = await c.call_tool(action.tool, mcp_args, raise_on_error=False)
        if res.is_error:
            text = res.content[0].text if res.content else "<no error text>"
            return ActionResult(
                tool=action.tool,
                args=args,
                dry_run=False,
                success=False,
                error=text,
                stderr=text,
            )
        # Server returns the same shape we'd produce locally (mode/severity/
        # incident_id added but we ignore those — the agent has its own).
        text = res.content[0].text if res.content else "{}"
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return ActionResult(
                tool=action.tool,
                args=args,
                dry_run=False,
                success=False,
                error="MCP returned non-JSON",
                stderr=text,
            )
        return ActionResult(
            tool=action.tool,
            args=args,
            dry_run=False,
            success=bool(data.get("success", True)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            error=data.get("error"),
        )

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP call failed (%s); falling back to direct dispatch", exc)
        return _dispatch(action, dry_run=False)


def _action_to_mcp_args(action: ProposedAction, args: dict[str, Any]) -> dict[str, Any]:
    """Translate ProposedAction.args (agent-internal names) to MCP-tool arg names."""
    if action.tool == "restart_pod":
        return {
            "pod": args.get("pod_name") or args.get("pod") or "",
            "namespace": args.get("namespace", ""),
        }
    if action.tool == "rollout_undo":
        return {
            "deployment": args.get("deployment_name") or args.get("deployment") or "",
            "namespace": args.get("namespace", ""),
        }
    if action.tool == "scale_deployment":
        return {
            "deployment": args.get("deployment_name") or args.get("deployment") or "",
            "namespace": args.get("namespace", ""),
            "replicas": int(args.get("replicas", 1) or 1),
        }
    if action.tool == "cordon_node":
        return {"node": args.get("node_name") or args.get("node") or ""}
    if action.tool == "uncordon_node":
        return {"node": args.get("node_name") or args.get("node") or ""}
    if action.tool == "drain_node":
        return {
            "node": args.get("node_name") or args.get("node") or "",
            "grace_period_s": int(args.get("grace_period_s", 60) or 60),
        }
    if action.tool == "page_human":
        return {"reason": args.get("reason", "no reason given")}
    return args


def node_verify(state: IncidentState) -> dict[str, Any]:
    action_result = state.get("action_result", {})
    # Dry-run actions are trivially "verified" — no cluster change happened.
    if action_result.get("dry_run", False):
        v = Verification(healed=True, notes="dry-run mode; no actual change to verify")
        out = v.model_dump()
        audit.emit(state["incident_id"], "verify", out)
        return {"verification": out}
    tool = action_result.get("tool")
    args = action_result.get("args", {})
    ns = args.get("namespace") or state.get("namespace") or "workloads"
    pod = args.get("pod_name") or state.get("pod") or ""
    deploy = args.get("deployment_name")
    # Node-scoped actions (cordon / drain) ARE healing actions — their
    # whole purpose is to mutate node state; kubectl-call success means
    # the heal happened. `healed=True` is correct here.
    if tool in {"cordon_node", "uncordon_node", "drain_node"}:
        v = Verification(healed=True, notes=f"{tool} requires no pod-readiness verification")
        audit.emit(state["incident_id"], "verify", v.model_dump())
        return {"verification": v.model_dump()}
    # page_human is NOT a healing action — it delegates the heal to a
    # human. The cluster is unchanged. Mark the incident as escalated so
    # the outcome chip is "Escalated" (not the misleading "Healed").
    # `healed=None` because we genuinely don't know whether the operator
    # will fix it.
    if tool == "page_human":
        v = Verification(
            healed=None,
            notes="page delivered to human; cluster state unchanged",
        )
        audit.emit(state["incident_id"], "verify", v.model_dump())
        return {"verification": v.model_dump(), "escalated": True}
    result = tools.verify_pod_recovered(ns, pod, deploy, timeout_s=30.0)
    audit.emit(state["incident_id"], "verify", result)
    return {"verification": result}


def node_escalate(state: IncidentState) -> dict[str, Any]:
    reason = state.get("escalation_reason") or "graph routed to escalate"
    pa_dict = state.get("proposed_action") or {}

    # Stale-event short-circuits arrive here too, but we MUST NOT page a
    # human for them — the underlying problem already healed on its own,
    # which is the opposite of "human attention needed". Logging is
    # sufficient; the audit still records the skip with full reasoning.
    stale = bool(state.get("stale_resolved"))
    if stale:
        log.info(
            "escalate: skipping page_human for stale-resolved incident %s", state.get("incident_id")
        )
        page_result = None
    else:
        page_result = tools.page_human(
            reason=reason,
            dry_run=False,
            incident_id=state.get("incident_id"),
            namespace=state.get("namespace"),
            pod=state.get("pod"),
            proposed_tool=(pa_dict.get("tool") if isinstance(pa_dict, dict) else None),
            severity=state.get("severity"),
        )

    # Prometheus counter — coarse-grained reason tag (bounded cardinality).
    try:
        from agent import metrics as _m

        pol = state.get("policy_decision") or {}
        reasons = pol.get("reasons", []) or []
        if stale:
            tag = "stale_resolved"
        elif any("L3" in r or "L4" in r for r in reasons):
            tag = "matrix_denial"
        elif any("agent_mode" in r for r in reasons):
            tag = "mode_denial"
        elif state.get("action_result") and not (state["action_result"] or {}).get("success"):
            tag = "execute_failed"
        elif state.get("verification") and (state["verification"] or {}).get("healed") is False:
            tag = "not_healed"
        elif (
            state.get("diagnosis")
            and float((state["diagnosis"] or {}).get("confidence") or 0)
            < settings.confidence_threshold
        ):
            tag = "low_confidence"
        else:
            tag = "other"
        _m.escalations_total.labels(reason=tag).inc()
    except Exception:  # noqa: BLE001
        pass
    # If we got here because of a matrix L3 denial (not a mode denial), stash
    # the proposed action as a pending approval so a human can override.
    pol = state.get("policy_decision") or {}
    if pa_dict and pol.get("allow") is False:
        reasons = pol.get("reasons", []) or []
        is_matrix_denial = any("L3" in r or "L4" in r for r in reasons)
        is_mode_denial = any("agent_mode" in r for r in reasons)
        if is_matrix_denial and not is_mode_denial:
            try:
                from agent import approvals

                approvals.register(
                    incident_id=state["incident_id"],
                    action=ProposedAction.model_validate(pa_dict),
                    severity=state.get("severity", "medium"),
                    namespace=state.get("namespace"),
                    denied_reasons=reasons,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to register pending approval: %s", exc)
    audit.emit(
        state["incident_id"],
        "escalate",
        {
            "reason": reason,
            "stale_resolved": stale,
            "page_delivery": (page_result.meta or {}).get("delivery") if page_result else None,
        },
    )
    return {"escalated": True}


# ===========================================================================
# Conditional edge routers.
# ===========================================================================


def _route_after_triage(state: IncidentState) -> str:
    """If triage flagged the event as stale + the target is healthy, skip
    the expensive LLM cycle and go straight to escalate with the
    pre-baked reason."""
    if state.get("stale_resolved"):
        return "escalate"
    return "next"


def _route_after_diagnose(state: IncidentState) -> str:
    """Gate the diagnose→plan_action edge.

    Phase 12 — instead of one global threshold, the per-rule threshold
    from `agent/calibration` wins:
      • Operator-pinned override (set via the SPA) is highest priority.
      • Learned threshold (rule's own p25 once we have ≥20 graded
        incidents) is the middle tier.
      • Global default (`settings.confidence_threshold`, default 0.6)
        is the fallback for new rules.

    Falls back to the global threshold on any calibration-store error
    so the graph stays usable even when the SQLite is unhealthy.
    """
    diag = state.get("diagnosis", {})
    conf = float(diag.get("confidence", 0.0))
    rule = state.get("rule")

    threshold = float(settings.confidence_threshold)
    source = "global"
    if rule:
        try:
            from agent.calibration import get_default_store
            from agent.metrics import calibration_threshold

            stats = get_default_store().get(rule)
            threshold = stats.effective_threshold
            source = (
                "override"
                if stats.threshold_override is not None
                else ("learned" if stats.n_graded >= 20 else "global")
            )
            calibration_threshold.labels(rule=rule).set(threshold)
        except Exception as exc:  # noqa: BLE001
            log.debug("calibration: threshold lookup failed (%s); using global default", exc)

    if conf < threshold:
        log.info(
            "diagnosis confidence %.2f < %.2f (rule=%s, source=%s) -> escalate",
            conf,
            threshold,
            rule,
            source,
        )
        return "escalate"
    return "plan_action"


def _route_after_policy(state: IncidentState) -> str:
    decision = PolicyDecision.model_validate(
        {
            "allow": state["policy_decision"]["allow"],
            "reasons": state["policy_decision"]["reasons"],
        }
    )
    if not decision.allow:
        return "escalate"
    return "execute"


def _route_after_execute(state: IncidentState) -> str:
    """Skip verify entirely if the action itself failed — go straight to
    escalate so the operator sees the kubectl error instead of a misleading
    'healed' from a name-prefix coincidence."""
    ar = state.get("action_result") or {}
    if not ar.get("success", False):
        return "escalate"
    return "verify"


def _route_after_verify(state: IncidentState) -> str:
    # If verify already marked the incident as escalated (e.g., page_human
    # was the proposed_action — the human owns the heal from here), end
    # cleanly. Routing to `escalate` would re-page for the same incident.
    if state.get("escalated"):
        return END
    v = state.get("verification", {})
    if not v.get("healed", False):
        return "escalate"
    return END


# ===========================================================================
# Tool dispatcher.
# ===========================================================================


def _dispatch(action: ProposedAction, dry_run: bool):
    tool = action.tool
    args = action.args or {}
    if tool == "restart_pod":
        return tools.restart_pod(
            pod_name=args.get("pod_name") or args.get("name", ""),
            namespace=args["namespace"],
            dry_run=dry_run,
        )
    if tool == "rollout_undo":
        return tools.rollout_undo(
            deployment_name=args.get("deployment_name") or args.get("name", ""),
            namespace=args["namespace"],
            dry_run=dry_run,
        )
    if tool == "scale_deployment":
        return tools.scale_deployment(
            deployment_name=args.get("deployment_name") or args.get("deployment", ""),
            namespace=args["namespace"],
            replicas=int(args.get("replicas", 1) or 1),
            dry_run=dry_run,
        )
    if tool == "cordon_node":
        return tools.cordon_node(
            node_name=args.get("node_name") or args.get("node", ""),
            dry_run=dry_run,
        )
    if tool == "uncordon_node":
        return tools.uncordon_node(
            node_name=args.get("node_name") or args.get("node", ""),
            dry_run=dry_run,
        )
    if tool == "drain_node":
        return tools.drain_node(
            node_name=args.get("node_name") or args.get("node", ""),
            grace_period_s=int(args.get("grace_period_s", 60) or 60),
            dry_run=dry_run,
        )
    if tool == "page_human":
        return tools.page_human(reason=args.get("reason", "no reason given"), dry_run=dry_run)
    raise ValueError(f"unknown tool {tool!r}")


# ===========================================================================
# Graph wiring.
# ===========================================================================


def build_graph():
    sg = StateGraph(IncidentState)
    # Every node is wrapped with `pipeline_trace.instrumented(...)` so node
    # entry/exit emits audit + bus events. Wrapping is uniform: no node has
    # special knowledge of the trace bus. The graph topology and node logic
    # are unchanged.
    from agent.pipeline_trace import instrumented as _inst

    sg.add_node("triage", _inst("triage", node_triage))
    sg.add_node("retrieve_context", _inst("retrieve_context", node_retrieve_context))
    sg.add_node("k8sgpt_analyze", _inst("k8sgpt_analyze", node_k8sgpt_analyze))
    sg.add_node(
        "enrich_with_baselines",
        _inst("enrich_with_baselines", node_enrich_with_baselines),
    )
    sg.add_node("diagnose", _inst("diagnose", node_diagnose))
    sg.add_node("plan_action", _inst("plan_action", node_plan_action))
    sg.add_node("policy_gate", _inst("policy_gate", node_policy_gate))
    sg.add_node("execute", _inst("execute", node_execute))
    sg.add_node("verify", _inst("verify", node_verify))
    sg.add_node("escalate", _inst("escalate", node_escalate))

    sg.set_entry_point("triage")
    sg.add_conditional_edges(
        "triage", _route_after_triage, {"escalate": "escalate", "next": "retrieve_context"}
    )
    sg.add_edge("retrieve_context", "k8sgpt_analyze")
    sg.add_edge("k8sgpt_analyze", "enrich_with_baselines")
    sg.add_edge("enrich_with_baselines", "diagnose")
    sg.add_conditional_edges(
        "diagnose", _route_after_diagnose, {"plan_action": "plan_action", "escalate": "escalate"}
    )
    sg.add_edge("plan_action", "policy_gate")
    sg.add_conditional_edges(
        "policy_gate", _route_after_policy, {"execute": "execute", "escalate": "escalate"}
    )
    sg.add_conditional_edges(
        "execute", _route_after_execute, {"verify": "verify", "escalate": "escalate"}
    )
    sg.add_conditional_edges("verify", _route_after_verify, {END: END, "escalate": "escalate"})
    sg.add_edge("escalate", END)
    return sg.compile()


# ===========================================================================
# Entrypoints.
# ===========================================================================


def _initial_state(event: dict[str, Any]) -> IncidentState:
    return {
        "incident_id": f"inc-{uuid.uuid4().hex[:10]}",
        "raw_event": event,
    }


def run_once(event: dict[str, Any]) -> IncidentState:
    """Run the graph synchronously for a single event. Returns the final state."""
    graph = build_graph()
    state = _initial_state(event)
    audit.emit(state["incident_id"], "received", {"event_keys": list(event.keys())})
    final = graph.invoke(state)
    audit.emit(
        final["incident_id"],
        "complete",
        {
            "escalated": final.get("escalated", False),
            "healed": final.get("verification", {}).get("healed", False),
        },
    )
    return final


async def serve() -> None:
    """Long-lived NATS subscriber. Imports nats lazily so the --once path
    doesn't pull in the asyncio dependency on every CLI invocation."""
    import nats

    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()

    # Ensure the stream exists (idempotent).
    try:
        await js.add_stream(name="INFRA", subjects=["infra.events.>"])
    except Exception as exc:  # noqa: BLE001 — JetStream may say "already exists"
        log.debug("add_stream noted: %s", exc)

    log.info("serve: subscribing to infra.events.> via JetStream")
    sub = await js.pull_subscribe("infra.events.>", durable="pocteo-agent")
    while True:
        msgs = await sub.fetch(1, timeout=60)
        for m in msgs:
            try:
                event = json.loads(m.data.decode("utf-8"))
                final = run_once(event)
                log.info(
                    "processed incident_id=%s escalated=%s",
                    final.get("incident_id"),
                    final.get("escalated", False),
                )
            except Exception:
                log.exception("graph crashed for message; nak'ing")
                await m.nak()
            else:
                await m.ack()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_scenario(scenario: int) -> dict[str, Any]:
    # Defer the import so we don't take a heavy dep on synthetic_events for --serve.
    from agent.synthetic_events import build_event

    return build_event(scenario)


def main() -> int:
    p = argparse.ArgumentParser(description="self-healing agent graph")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--once",
        action="store_true",
        help="run the graph once against the chosen synthetic scenario",
    )
    g.add_argument("--serve", action="store_true", help="long-lived NATS subscriber")
    p.add_argument(
        "--scenario",
        type=int,
        default=1,
        help="1..5 — which synthetic Falco scenario to use with --once",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    configure_logging("DEBUG" if args.verbose else None)
    mode_store.start()
    warmup_chat_model()

    if args.once:
        event = _load_scenario(args.scenario)
        final = run_once(event)
        print(json.dumps(_summary(final), indent=2))
        return 0
    if args.serve:
        try:
            asyncio.run(serve())
        except KeyboardInterrupt:
            log.info("shutdown")
        return 0
    return 2


def _summary(state: IncidentState) -> dict[str, Any]:
    return {
        "incident_id": state.get("incident_id"),
        "rule": state.get("rule"),
        "severity": state.get("severity"),
        "namespace": state.get("namespace"),
        "pod": state.get("pod"),
        "agent_mode": state.get("agent_mode"),
        "diagnosis": state.get("diagnosis"),
        "proposed_action": state.get("proposed_action"),
        "policy_decision": state.get("policy_decision"),
        "action_result": state.get("action_result"),
        "verification": state.get("verification"),
        "escalated": state.get("escalated", False),
    }


if __name__ == "__main__":
    sys.exit(main())
