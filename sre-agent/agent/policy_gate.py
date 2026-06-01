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
_DEFAULT_KIND_BY_SCOPE = {
    "pod": "Pod",
    "deploy": "Deployment",
    "ns": "Namespace",
    "n/a": "None",
}


def _target_for(action: ProposedAction, namespace: str | None) -> dict[str, Any]:
    args = action.args or {}
    # Each tool's args use slightly different key names; normalise here so the
    # policy contract is stable (target = {namespace, kind, name}).
    name = (
        args.get("pod_name")
        or args.get("name")
        or args.get("deployment_name")
        or args.get("deploy")
        or "n/a"
    )
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
