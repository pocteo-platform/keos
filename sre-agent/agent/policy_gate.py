"""Client for the OPA policy gate.

Builds an input document from the graph state, POSTs to /v1/data/agent/authz,
returns a PolicyDecision. The agent treats unreachable-OPA as a hard deny
(fail closed) — never proceed with an action when the gate cannot answer.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.config import settings
from agent.schemas import PolicyDecision, ProposedAction

log = logging.getLogger(__name__)

# Map ProposedAction.scope to the target shape OPA expects.
# Phase 9.2 SRE action catalog added node-scoped actions (cordon_node,
# uncordon_node, drain_node). Their target is a Node, not a Pod — the
# scope-to-kind table didn't carry `node` and the audit log misreported
# every node action's `target.kind` as `"Unknown"`. OPA doesn't gate on
# kind today, but downstream tooling (chain verifier, learning loop)
# does.
_DEFAULT_KIND_BY_SCOPE = {
    "pod": "Pod",
    "deploy": "Deployment",
    "node": "Node",
    "ns": "Namespace",
    "n/a": "None",
}


def _target_for(action: ProposedAction, namespace: str | None) -> dict[str, Any]:
    args = action.args or {}
    # Each tool's args use slightly different key names; normalise here so
    # the policy contract is stable (target = {namespace, kind, name}).
    # Arg search order is intentional: pod_name first (most common scope),
    # then node_name (cordon_node / drain_node / uncordon_node — Phase 9.2),
    # then deployment_name (rollout_undo / scale_deployment), then a bare
    # `name` field. Without `node_name`, every node-targeted action's
    # `target.name` was falling through to "n/a" in the audit log.
    name = (
        args.get("pod_name")
        or args.get("node_name")
        or args.get("deployment_name")
        or args.get("deploy")
        or args.get("name")
        or "n/a"
    )
    # Node-scoped actions don't carry a namespace — clear it so the audit
    # log doesn't pin a node action to whatever namespace the incident's
    # diagnose happened to target. `protected_namespaces` matches on
    # `target.namespace`, and an empty string is not in that set, so node
    # actions clear the gate as the Rego matrix intends.
    if action.scope == "node":
        ns: str | None = ""
    else:
        ns = args.get("namespace") or namespace or "n/a"
    return {
        "namespace": ns,
        "kind": _DEFAULT_KIND_BY_SCOPE.get(action.scope, "Unknown"),
        "name": name,
    }


def evaluate(
    action: ProposedAction,
    severity: str,
    agent_mode: str,
    namespace: str | None,
) -> PolicyDecision:
    """Synchronous OPA evaluation. Returns deny+reason if OPA is unreachable."""
    payload = {
        "input": {
            "action": action.tool,
            "severity": severity,
            "agent_mode": agent_mode,
            "target": _target_for(action, namespace),
        }
    }
    url = f"{settings.opa_url}/v1/data/agent/authz"
    try:
        resp = httpx.post(url, json=payload, timeout=5.0)
        resp.raise_for_status()
    except httpx.RequestError as exc:
        log.error("OPA unreachable at %s: %s — failing closed", url, exc)
        _record_decision("error", action.tool, severity)
        return PolicyDecision(allow=False, reasons=[f"OPA unreachable: {exc}"])

    body = resp.json().get("result", {})
    decision = PolicyDecision(
        allow=bool(body.get("allow", False)),
        reasons=list(body.get("reasons", [])),
    )
    _record_decision("allow" if decision.allow else "deny", action.tool, severity)
    return decision


def _record_decision(verdict: str, action: str, severity: str) -> None:
    """Increment the Prometheus counter. Wrapped so a metrics-module import
    failure (e.g., during a test that stubs prometheus_client) doesn't
    propagate into the policy evaluation path."""
    try:
        from agent import metrics as _m

        _m.policy_decisions_total.labels(
            decision=verdict,
            action=action,
            severity=severity,
        ).inc()
    except Exception:  # noqa: BLE001
        pass
