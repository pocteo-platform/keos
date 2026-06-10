"""FastMCP server that exposes the 5 action tools.

Each tool follows the same pattern:
    1. Build a ProposedAction shape for OPA input.
    2. Read the current AgentMode (from the same ModeStore watch the agent uses).
    3. Call policy_gate.evaluate(); raise ToolError if denied.
    4. Dispatch to the corresponding agent.tools.<fn> with dry_run=False
       (MCP only ever does real actions — observe-only/frozen are denied by OPA).
    5. Emit an audit record with source: "mcp".
    6. Return the ActionResult as a JSON dict.

The agent's graph also has its own policy_gate node. When the agent calls
MCP, OPA is checked TWICE. That's defense-in-depth, not a bug.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from agent import tools as agent_tools
from agent.policy_gate import evaluate as opa_evaluate
from agent.schemas import ProposedAction
from mcp_server.audit import MCPAuditLogger

mcp = FastMCP("self-healing-actions")
audit = MCPAuditLogger()

# MCP tools are typically invoked by operators (Claude Desktop) without a
# Falco event, so we don't always have a real severity. The agent caller
# CAN pass a severity arg to convey what its Falco event said; the default
# is "high", which lands in L1/L2 (allowed) for every tool in the matrix.
DEFAULT_SEVERITY = "high"


def _new_incident_id() -> str:
    return f"mcp-{uuid.uuid4().hex[:10]}"


def _gate(action: ProposedAction, severity: str) -> str:
    """Common gate: read mode, call OPA, raise on deny. Returns the mode."""
    # Local import to avoid blocking module import if the mode_store can't
    # connect (e.g., during in-process unit tests with no cluster).
    from agent.mode_store import mode_store as live_mode_store

    try:
        mode = live_mode_store.get()
    except Exception:  # noqa: BLE001
        mode = os.environ.get("FALLBACK_AGENT_MODE", "observe-only")

    decision = opa_evaluate(action, severity, mode, action.args.get("namespace"))
    if not decision.allow:
        raise ToolError(f"policy denied {action.tool}: " + " | ".join(decision.reasons))
    return mode


def _emit(incident_id: str, event_type: str, payload: dict[str, Any]) -> None:
    try:
        audit.emit(incident_id, event_type, payload)
    except Exception:  # noqa: BLE001
        # Never let audit failures cascade into tool failures.
        pass


# ---------------------------------------------------------------------------
# Tools.
# ---------------------------------------------------------------------------


@mcp.tool()
def restart_pod(pod: str, namespace: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Delete a pod so its owning controller respawns a fresh replica.

    Use for: in-process compromise (shell, in-memory implant) where the
    image itself is trusted. Never for ImagePullBackOff (the new pod will
    hit the same failure).
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="restart_pod",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=True,
        rationale="MCP-driven restart_pod call",
    )
    mode = _gate(action, severity)
    result = agent_tools.restart_pod(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"restart_pod failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def rollout_undo(
    deployment: str, namespace: str, severity: str = DEFAULT_SEVERITY
) -> dict[str, Any]:
    """Revert a Deployment to its previous ReplicaSet.

    Use for: bad rollouts (ErrImagePull on a new tag, regression). Cheap
    and reversible.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="rollout_undo",
        args={"deployment_name": deployment, "namespace": namespace},
        scope="deploy",
        reversible=True,
        rationale="MCP-driven rollout_undo call",
    )
    mode = _gate(action, severity)
    result = agent_tools.rollout_undo(
        deployment_name=deployment, namespace=namespace, dry_run=False
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"rollout_undo failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def scale_deployment(
    deployment: str, namespace: str, replicas: int, severity: str = DEFAULT_SEVERITY
) -> dict[str, Any]:
    """Set a Deployment's replica count.

    Use for: traffic surge, hot-pod hot-spotting, capacity head-room
    before a rollout. Reversible: operator (or HPA) can re-tune.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="scale_deployment",
        args={"deployment_name": deployment, "namespace": namespace, "replicas": replicas},
        scope="deploy",
        reversible=True,
        rationale="MCP-driven scale_deployment call",
    )
    mode = _gate(action, severity)
    result = agent_tools.scale_deployment(
        deployment_name=deployment,
        namespace=namespace,
        replicas=replicas,
        dry_run=False,
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"scale_deployment failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def cordon_node(node: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Mark a node unschedulable.

    Use for: node showing MemoryPressure / DiskPressure / PID pressure
    before it cascades into evictions. Does not evict existing pods —
    pair with drain_node for full evacuation. Reversible via uncordon.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="cordon_node",
        args={"node_name": node},
        scope="node",
        reversible=True,
        rationale="MCP-driven cordon_node call",
    )
    mode = _gate(action, severity)
    result = agent_tools.cordon_node(node_name=node, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"cordon_node failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def uncordon_node(node: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Re-enable scheduling on a previously cordoned node.

    Use for: cluster stuck with `FailedScheduling: nodes are unschedulable`
    after an earlier cordon/drain. Only re-enables scheduling — does NOT
    move pods or change anything else. Safe at any severity (matrix L1
    across the board).
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="uncordon_node",
        args={"node_name": node},
        scope="node",
        reversible=True,
        rationale="MCP-driven uncordon_node call",
    )
    mode = _gate(action, severity)
    result = agent_tools.uncordon_node(node_name=node, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"uncordon_node failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def drain_node(
    node: str, grace_period_s: int = 60, severity: str = DEFAULT_SEVERITY
) -> dict[str, Any]:
    """Cordon + evict all evictable pods from a node.

    Use for: confirmed node failure where workloads should reschedule
    elsewhere. Higher blast radius than cordon — only allowed at high
    severity in the OPA matrix.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="drain_node",
        args={"node_name": node, "grace_period_s": grace_period_s},
        scope="node",
        reversible=True,
        rationale="MCP-driven drain_node call",
    )
    mode = _gate(action, severity)
    result = agent_tools.drain_node(node_name=node, grace_period_s=grace_period_s, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"drain_node failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def tune_hpa(
    hpa_name: str,
    namespace: str,
    min_replicas: int | None = None,
    max_replicas: int | None = None,
    target_cpu: int | None = None,
    severity: str = DEFAULT_SEVERITY,
) -> dict[str, Any]:
    """Phase 14 — adjust a HorizontalPodAutoscaler's bounds + target CPU.

    Use for: forecasted traffic surge (raise max before reactive HPA
    trips), rightsizing a chronically over/under-provisioned workload.
    All three knobs optional — pass only what you want to change.
    """
    inc = _new_incident_id()
    args = {
        "hpa_name": hpa_name,
        "namespace": namespace,
        "min_replicas": min_replicas,
        "max_replicas": max_replicas,
        "target_cpu": target_cpu,
    }
    action = ProposedAction(
        tool="tune_hpa",
        args=args,
        scope="deploy",
        reversible=True,
        rationale="MCP-driven tune_hpa call",
    )
    mode = _gate(action, severity)
    result = agent_tools.tune_hpa(
        hpa_name=hpa_name,
        namespace=namespace,
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        target_cpu=target_cpu,
        dry_run=False,
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"tune_hpa failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def set_resources(
    deployment_name: str,
    namespace: str,
    container: str,
    requests: dict[str, str] | None = None,
    limits: dict[str, str] | None = None,
    severity: str = DEFAULT_SEVERITY,
) -> dict[str, Any]:
    """Phase 14 — patch a Deployment container's resource requests/limits.

    Use for: forecasted OOM (raise memory limit), rightsizing
    over-provisioned containers (lower requests). Strategic-merge
    patch — preserves env, volumeMounts, securityContext, etc.
    """
    inc = _new_incident_id()
    args = {
        "deployment_name": deployment_name,
        "namespace": namespace,
        "container": container,
        "requests": requests,
        "limits": limits,
    }
    action = ProposedAction(
        tool="set_resources",
        args=args,
        scope="deploy",
        reversible=True,
        rationale="MCP-driven set_resources call",
    )
    mode = _gate(action, severity)
    result = agent_tools.set_resources(
        deployment_name=deployment_name,
        namespace=namespace,
        container=container,
        requests=requests,
        limits=limits,
        dry_run=False,
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"set_resources failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def evict_workload(
    namespace: str, label_selector: str, severity: str = DEFAULT_SEVERITY
) -> dict[str, Any]:
    """Phase 14 — delete every pod matching `label_selector` in `namespace`.

    Soft restart for stateless workloads. The scheduler reschedules the
    deleted pods — pair with cordon_node first if you want them moved
    off the source node. Empty selector is rejected.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="evict_workload",
        args={"namespace": namespace, "label_selector": label_selector},
        scope="ns",
        reversible=True,
        rationale="MCP-driven evict_workload call",
    )
    mode = _gate(action, severity)
    result = agent_tools.evict_workload(
        namespace=namespace, label_selector=label_selector, dry_run=False
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"evict_workload failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def revert_drift(
    kind: str,
    namespace: str,
    name: str,
    sot_source: str,
    severity: str = DEFAULT_SEVERITY,
) -> dict[str, Any]:
    """Phase 15 — re-apply the source-of-truth manifest to the cluster.

    Use for: GitOps drift events. `sot_source` is the pointer the
    drift detector stashed in the event payload (`drift.source` field).
    Format is `path[#doc_index]` for the v1 ManifestsSource provider.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="revert_drift",
        args={
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "sot_source": sot_source,
        },
        scope="ns",
        reversible=True,
        rationale="MCP-driven revert_drift call",
    )
    mode = _gate(action, severity)
    result = agent_tools.revert_drift(
        kind=kind,
        namespace=namespace,
        name=name,
        sot_source=sot_source,
        dry_run=False,
    )
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"revert_drift failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def page_human(reason: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Record a structured page-human event in the audit log.

    Use when: low confidence, policy denial, unknown situation. The PoC
    just logs; production would call PagerDuty / Slack here.
    """
    inc = _new_incident_id()
    action = ProposedAction(
        tool="page_human",
        args={"reason": reason},
        scope="n/a",
        reversible=True,
        rationale="MCP-driven page_human call",
    )
    mode = _gate(action, severity)
    result = agent_tools.page_human(reason=reason, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    return payload


# ---------------------------------------------------------------------------
# Security domain tools (Phase 21). Same pattern as above — OPA gate, then
# dispatch to agent.security_tools.*. These are what the agent's execute node
# calls over MCP for a security incident in active mode; without them, live
# containment would 404 on the MCP server.
# ---------------------------------------------------------------------------


@mcp.tool()
def isolate_pod(pod: str, namespace: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Cut a compromised pod off the network (deny-all NetworkPolicy +
    quarantine label) and hold it for forensics. Reversible: delete the
    NetworkPolicy + remove the label. The default containment for an active
    runtime threat (reverse shell, miner)."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="isolate_pod",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=True,
        rationale="MCP-driven isolate_pod call",
    )
    mode = _gate(action, severity)
    result = security_tools.isolate_pod(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"isolate_pod failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def apply_network_policy(
    pod: str, namespace: str, severity: str = DEFAULT_SEVERITY
) -> dict[str, Any]:
    """Block a pod's outbound traffic (deny-egress NetworkPolicy, DNS
    excepted) while keeping it observable. The data-exfil containment.
    Reversible: delete the NetworkPolicy."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="apply_network_policy",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=True,
        rationale="MCP-driven apply_network_policy call",
    )
    mode = _gate(action, severity)
    result = security_tools.apply_network_policy(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"apply_network_policy failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def kill_pod(pod: str, namespace: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Force-delete a compromised pod (irreversible — process state is lost).
    Use only when containment-in-place is insufficient and the threat is
    critical. The owning controller respawns a clean pod."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="kill_pod",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=False,
        rationale="MCP-driven kill_pod call",
    )
    mode = _gate(action, severity)
    result = security_tools.kill_pod(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"kill_pod failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def quarantine_pod(pod: str, namespace: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Isolate a pod AND annotate it for a forensics hold (preserve evidence).
    Reversible: remove the label + annotation + delete the NetworkPolicy."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="quarantine_pod",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=True,
        rationale="MCP-driven quarantine_pod call",
    )
    mode = _gate(action, severity)
    result = security_tools.quarantine_pod(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"quarantine_pod failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def capture_forensics(pod: str, namespace: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Read-only evidence capture: annotate the pod + dump its recent logs
    into the audit trail. Safe to run anytime; pair with a containment
    action."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="capture_forensics",
        args={"pod_name": pod, "namespace": namespace},
        scope="pod",
        reversible=True,
        rationale="MCP-driven capture_forensics call",
    )
    mode = _gate(action, severity)
    result = security_tools.capture_forensics(pod_name=pod, namespace=namespace, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    if not result.success:
        raise ToolError(f"capture_forensics failed: {result.error or result.stderr}")
    return payload


@mcp.tool()
def page_security(reason: str, severity: str = DEFAULT_SEVERITY) -> dict[str, Any]:
    """Page the security / incident-response channel. The security-domain
    escalation tool — always allowed by the OPA matrix."""
    from agent import security_tools

    inc = _new_incident_id()
    action = ProposedAction(
        tool="page_security",
        args={"reason": reason},
        scope="n/a",
        reversible=True,
        rationale="MCP-driven page_security call",
    )
    mode = _gate(action, severity)
    result = security_tools.page_security(reason=reason, dry_run=False)
    payload = result.model_dump() | {"mode": mode, "severity": severity, "incident_id": inc}
    _emit(inc, "execute", payload)
    return payload
