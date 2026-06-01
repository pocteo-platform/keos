"""In-memory pending-approval queue for L3-denied actions.

When the agent's `policy_gate` returns deny with reasons that include
"is L3", `node_escalate` calls `register()` BEFORE paging human. The
operator then sees the entry in the Control view and clicks Approve or
Deny.

Approve = call MCP with `severity=critical` (bumps the matrix to allow
the action) plus a `human_override=true` audit marker. The override is
visible in the forensic stream — we never bypass OPA invisibly.

Deny = record the reason in audit; do nothing else.

State is in-memory + a `agent/approvals/pending.json` snapshot for
crash-safety. Resetting the agent clears in-flight approvals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastmcp import Client

from agent.api_models import PendingApproval
from agent.audit import AuditLogger
from agent.config import settings
from agent.schemas import ActionResult, ProposedAction

log = logging.getLogger(__name__)


def _path() -> Path:
    base = Path(getattr(settings, "audit_log_path", "agent/audit.log"))
    d = base.parent / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d / "pending.json"


_lock = threading.Lock()
_audit = AuditLogger()


def _load_raw() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_raw(items: list[dict[str, Any]]) -> None:
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Public API used by the graph's escalate node + the FastAPI endpoints.
# ---------------------------------------------------------------------------


def register(
    incident_id: str,
    action: ProposedAction,
    severity: str,
    namespace: str | None,
    denied_reasons: list[str],
) -> None:
    """Called by node_escalate when the denial was a matrix L3 (i.e. a
    human override could plausibly unblock it). Mode-gate denials are
    NOT registered — those require flipping the CRD, not Approve."""
    entry = {
        "incident_id": incident_id,
        "ts": _now(),
        "proposed_action": action.model_dump(),
        "severity": severity,
        "namespace": namespace,
        "denied_reasons": denied_reasons,
    }
    with _lock:
        items = [it for it in _load_raw() if it.get("incident_id") != incident_id]
        items.append(entry)
        _save_raw(items)
    _audit.emit(incident_id, "approval_pending", {"reasons": denied_reasons, "tool": action.tool})
    log.info("approval registered for %s (%s)", incident_id, action.tool)


def list_pending() -> list[PendingApproval]:
    return [PendingApproval.model_validate(it) for it in _load_raw()]


def remove(incident_id: str) -> dict[str, Any] | None:
    with _lock:
        items = _load_raw()
        match = next((it for it in items if it.get("incident_id") == incident_id), None)
        if match is None:
            return None
        items = [it for it in items if it.get("incident_id") != incident_id]
        _save_raw(items)
    return match


def approve(incident_id: str) -> dict[str, Any]:
    """Re-dispatch the proposed action via MCP with a human-override marker.

    We call MCP with the original action + the highest sensible severity
    so the matrix allows it. The MCP server still calls OPA — defense in
    depth — and OPA will allow because the matrix at "critical" passes
    every action. The override is logged via the audit `human_override`
    marker so a forensic reviewer sees both that the operator approved
    AND why the agent was denied originally.
    """
    entry = remove(incident_id)
    if entry is None:
        return {"approved": False, "executed": False, "reason": "no such pending approval"}

    action = ProposedAction.model_validate(entry["proposed_action"])
    # Bump severity to a level that passes the matrix everywhere we care.
    override_severity = "critical"
    mcp_url = os.environ.get("MCP_URL", "http://localhost:8090/mcp")  # noqa: F821
    args = action.args or {}
    result = asyncio.run(_call_mcp(mcp_url, action, args, override_severity))

    _audit.emit(
        incident_id,
        "approval_granted",
        {
            "tool": action.tool,
            "args": args,
            "original_reasons": entry.get("denied_reasons", []),
            "override_severity": override_severity,
            "human_override": True,
            "action_result": result.model_dump(),
        },
    )

    return {
        "approved": True,
        "executed": result.success,
        "action_result": result.model_dump(),
        "reason": None,
    }


def deny(incident_id: str, reason: str) -> dict[str, Any]:
    entry = remove(incident_id)
    if entry is None:
        return {"approved": False, "executed": False, "reason": "no such pending approval"}
    _audit.emit(
        incident_id,
        "approval_denied",
        {"reason": reason, "tool": entry["proposed_action"].get("tool")},
    )
    return {"approved": False, "executed": False, "reason": reason}


# ---------------------------------------------------------------------------
# MCP dispatch helper. Kept here (rather than in agent/graph.py) so the
# import graph stays: approvals -> mcp; graph -> mcp; graph -> approvals.
# ---------------------------------------------------------------------------

# Local import is fine because os is std-lib.
import os  # noqa: E402


def _action_to_mcp_args(action: ProposedAction, args: dict[str, Any]) -> dict[str, Any]:
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
    if action.tool == "drain_node":
        return {
            "node": args.get("node_name") or args.get("node") or "",
            "grace_period_s": int(args.get("grace_period_s", 60) or 60),
        }
    if action.tool == "page_human":
        return {"reason": args.get("reason", "no reason given")}
    return args


async def _call_mcp(
    url: str, action: ProposedAction, args: dict[str, Any], severity: str
) -> ActionResult:
    mcp_args = _action_to_mcp_args(action, args) | {"severity": severity}
    try:
        async with Client(url) as c:
            res = await c.call_tool(action.tool, mcp_args, raise_on_error=False)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(
            tool=action.tool,
            args=args,
            dry_run=False,
            success=False,
            error=f"MCP call failed: {exc}",
        )
    if res.is_error:
        text = res.content[0].text if res.content else "<no text>"
        return ActionResult(
            tool=action.tool, args=args, dry_run=False, success=False, error=text, stderr=text
        )
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
