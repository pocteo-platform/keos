"""Hermetic tests for the MCP server.

Spins up the FastMCP server in-process via `fastmcp.Client(mcp)` and exercises
each tool through three paths:

  (a) allow path in active mode + workloads ns -> success
  (b) deny path observe-only -> ToolError mentioning mode
  (c) deny path kube-system ns -> ToolError mentioning protected

Plus a `tools/list` schema sanity check.

External dependencies (kubectl, OPA, mode_store, audit) are all stubbed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

import mcp_server.server as srv
from agent.schemas import ActionResult, PolicyDecision

# ---------------------------------------------------------------------------
# Fixtures wiring the server's dependencies to controllable stubs.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_mode(monkeypatch):
    """Patch the mode_store import inside _gate. Default = active."""
    box = {"mode": "active"}

    fake_ms = MagicMock()
    fake_ms.get = lambda: box["mode"]

    # _gate does `from agent.mode_store import mode_store as live_mode_store`.
    # We patch that attribute on agent.mode_store so both `import` paths see it.
    import agent.mode_store as ms

    monkeypatch.setattr(ms, "mode_store", fake_ms)
    return box


@pytest.fixture
def stub_opa(monkeypatch):
    """Patch agent.policy_gate.evaluate (referenced via mcp_server.server.opa_evaluate)
    so each test controls the decision. Default = allow."""
    box = {"decision": PolicyDecision(allow=True, reasons=[])}

    def set_decision(allow: bool, reasons: list[str] | None = None):
        box["decision"] = PolicyDecision(allow=allow, reasons=reasons or [])

    def fake_eval(action, severity, agent_mode, namespace):
        # Honour the mode + protected-namespace gates inside the stub so
        # the MCP server's behaviour mirrors real OPA without standing it up.
        if agent_mode != "active":
            return PolicyDecision(
                allow=False,
                reasons=[f'agent_mode is "{agent_mode}" (must be "active" to execute)'],
            )
        if (namespace or "") in {"kube-system", "kube-public", "security"}:
            return PolicyDecision(
                allow=False,
                reasons=[f'namespace "{namespace}" is protected'],
            )
        return box["decision"]

    monkeypatch.setattr(srv, "opa_evaluate", fake_eval)
    return set_decision


@pytest.fixture
def stub_tools(monkeypatch):
    """Patch agent.tools.* so we don't actually shell out to kubectl."""
    calls: dict[str, list[dict[str, Any]]] = {}

    def make_stub(name: str, default_args: dict[str, Any]):
        def fake(**kwargs):
            calls.setdefault(name, []).append(kwargs)
            return ActionResult(
                tool=name,
                args={**default_args, **kwargs},
                dry_run=False,
                success=True,
                stdout=f"stub-{name} ok",
            )

        return fake

    monkeypatch.setattr(srv.agent_tools, "restart_pod", make_stub("restart_pod", {}))
    monkeypatch.setattr(srv.agent_tools, "rollout_undo", make_stub("rollout_undo", {}))
    monkeypatch.setattr(srv.agent_tools, "scale_deployment", make_stub("scale_deployment", {}))
    monkeypatch.setattr(srv.agent_tools, "cordon_node", make_stub("cordon_node", {}))
    monkeypatch.setattr(srv.agent_tools, "drain_node", make_stub("drain_node", {}))
    monkeypatch.setattr(srv.agent_tools, "page_human", make_stub("page_human", {}))
    return calls


@pytest.fixture
def stub_audit(monkeypatch, tmp_path):
    """Redirect the MCP audit log to a tmp file."""
    p = tmp_path / "audit.log"
    from mcp_server.audit import MCPAuditLogger

    monkeypatch.setattr(srv, "audit", MCPAuditLogger(path=str(p)))
    return p


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


async def _list_tools() -> list:
    async with Client(srv.mcp) as c:
        return await c.list_tools()


async def _call(name: str, args: dict[str, Any]):
    # raise_on_error=False so we can assert on the returned CallToolResult
    # whether is_error is True. Default behaviour raises ToolError.
    async with Client(srv.mcp) as c:
        return await c.call_tool(name, args, raise_on_error=False)


# ---------------------------------------------------------------------------
# tools/list schema sanity check.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lists_all_tools_with_schemas() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    # 11 SRE actions + 6 Phase-21 security tools = 17.
    assert names == {
        "restart_pod",
        "rollout_undo",
        "scale_deployment",
        "cordon_node",
        "uncordon_node",
        "drain_node",
        # Phase 14 — predictive / rightsizing tools.
        "tune_hpa",
        "set_resources",
        "evict_workload",
        # Phase 15 — GitOps drift revert.
        "revert_drift",
        "page_human",
        # Phase 21 — security domain containment + escalation.
        "isolate_pod",
        "apply_network_policy",
        "kill_pod",
        "quarantine_pod",
        "capture_forensics",
        "page_security",
    }
    by_name = {t.name: t for t in tools}
    # restart_pod needs pod + namespace.
    assert "pod" in by_name["restart_pod"].inputSchema["properties"]
    assert "namespace" in by_name["restart_pod"].inputSchema["properties"]
    # rollout_undo needs deployment + namespace.
    assert "deployment" in by_name["rollout_undo"].inputSchema["properties"]
    # scale_deployment needs deployment + replicas.
    assert "deployment" in by_name["scale_deployment"].inputSchema["properties"]
    assert "replicas" in by_name["scale_deployment"].inputSchema["properties"]
    # cordon + drain need a node arg.
    assert "node" in by_name["cordon_node"].inputSchema["properties"]
    assert "node" in by_name["drain_node"].inputSchema["properties"]
    # Phase 14 schemas — verify each carries its distinctive arg.
    assert "hpa_name" in by_name["tune_hpa"].inputSchema["properties"]
    assert "container" in by_name["set_resources"].inputSchema["properties"]
    assert "label_selector" in by_name["evict_workload"].inputSchema["properties"]
    # Phase 15 — revert_drift carries kind + sot_source.
    assert "sot_source" in by_name["revert_drift"].inputSchema["properties"]
    assert "kind" in by_name["revert_drift"].inputSchema["properties"]
    # page_human needs reason.
    assert "reason" in by_name["page_human"].inputSchema["properties"]
    # Phase 21 — security tools carry pod + namespace (page_security: reason).
    assert "pod" in by_name["isolate_pod"].inputSchema["properties"]
    assert "namespace" in by_name["isolate_pod"].inputSchema["properties"]
    assert "pod" in by_name["kill_pod"].inputSchema["properties"]
    assert "reason" in by_name["page_security"].inputSchema["properties"]


# ---------------------------------------------------------------------------
# Per-tool allow / deny-by-mode / deny-by-ns matrix.
# ---------------------------------------------------------------------------

TOOL_CASES = [
    ("restart_pod", {"pod": "nginx-x", "namespace": "workloads"}),
    ("rollout_undo", {"deployment": "nginx-demo", "namespace": "workloads"}),
    ("scale_deployment", {"deployment": "nginx-demo", "namespace": "workloads", "replicas": 4}),
    ("cordon_node", {"node": "k3d-poc-server-0"}),
    ("drain_node", {"node": "k3d-poc-server-0"}),
    ("page_human", {"reason": "something off"}),
]


@pytest.mark.parametrize(("tool", "args"), TOOL_CASES, ids=[c[0] for c in TOOL_CASES])
@pytest.mark.asyncio
async def test_allow_path_active_mode(
    tool: str,
    args: dict[str, Any],
    stub_mode,
    stub_opa,
    stub_tools,
    stub_audit,
) -> None:
    stub_mode["mode"] = "active"
    res = await _call(tool, args)
    assert res.is_error is False
    # The returned payload includes mode and success.
    text = res.content[0].text if res.content else ""
    payload = json.loads(text)
    assert payload["success"] is True
    assert payload["mode"] == "active"


@pytest.mark.parametrize(("tool", "args"), TOOL_CASES, ids=[c[0] for c in TOOL_CASES])
@pytest.mark.asyncio
async def test_deny_in_observe_only(
    tool: str,
    args: dict[str, Any],
    stub_mode,
    stub_opa,
    stub_tools,
    stub_audit,
) -> None:
    stub_mode["mode"] = "observe-only"
    res = await _call(tool, args)
    assert res.is_error is True
    text = res.content[0].text if res.content else ""
    assert "observe-only" in text or "agent_mode" in text


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # page_human / cordon_node / drain_node have no namespace, so the
        # protected-ns gate is N/A. Restrict to namespace-bearing tools.
        ("restart_pod", {"pod": "x", "namespace": "kube-system"}),
        ("rollout_undo", {"deployment": "x", "namespace": "kube-system"}),
        ("scale_deployment", {"deployment": "x", "namespace": "kube-system", "replicas": 1}),
    ],
)
@pytest.mark.asyncio
async def test_deny_in_protected_namespace(
    tool: str,
    args: dict[str, Any],
    stub_mode,
    stub_opa,
    stub_tools,
    stub_audit,
) -> None:
    stub_mode["mode"] = "active"
    res = await _call(tool, args)
    assert res.is_error is True
    text = res.content[0].text if res.content else ""
    assert "protected" in text or args["namespace"] in text


@pytest.mark.asyncio
async def test_kubectl_failure_surfaces_as_tool_error(
    stub_mode,
    stub_opa,
    monkeypatch,
    stub_audit,
) -> None:
    """If the underlying kubectl call fails, the MCP tool must surface a
    ToolError so the caller doesn't think the action succeeded."""
    stub_mode["mode"] = "active"

    def failing(*args, **kwargs):
        return ActionResult(
            tool="restart_pod",
            args=kwargs,
            dry_run=False,
            success=False,
            error="kubectl exit 1",
            stderr="pod not found",
        )

    monkeypatch.setattr(srv.agent_tools, "restart_pod", failing)
    res = await _call("restart_pod", {"pod": "missing", "namespace": "workloads"})
    assert res.is_error is True
    text = res.content[0].text if res.content else ""
    assert "restart_pod failed" in text


@pytest.mark.asyncio
async def test_audit_records_source_mcp(
    stub_mode,
    stub_opa,
    stub_tools,
    stub_audit,
) -> None:
    """Every MCP-driven tool call must write an audit record with
    source: "mcp" so it's distinguishable from agent-driven actions."""
    stub_mode["mode"] = "active"
    await _call("restart_pod", {"pod": "nginx-x", "namespace": "workloads"})
    lines = stub_audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    rec = json.loads(lines[-1])
    assert rec["payload"]["source"] == "mcp"
    assert rec["event_type"] == "execute"


# ---------------------------------------------------------------------------
# Phase 21 — security domain tools over MCP. Proves the agent's live (non-
# dry-run) containment path actually dispatches; without these tools on the
# MCP server, a security incident in active mode would 404.
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_security_tools(monkeypatch):
    """Patch agent.security_tools.* so the MCP security tools don't shell out
    to kubectl. The MCP functions import the module lazily, so patching the
    module attribute is enough."""
    import agent.security_tools as st

    calls: dict[str, list[dict[str, Any]]] = {}

    def make_stub(name: str):
        def fake(**kwargs):
            calls.setdefault(name, []).append(kwargs)
            return ActionResult(
                tool=name, args=kwargs, dry_run=False, success=True, stdout=f"stub-{name} ok"
            )

        return fake

    for name in (
        "isolate_pod",
        "apply_network_policy",
        "kill_pod",
        "quarantine_pod",
        "capture_forensics",
        "page_security",
    ):
        monkeypatch.setattr(st, name, make_stub(name))
    return calls


SECURITY_TOOL_CASES = [
    ("isolate_pod", {"pod": "nginx-x", "namespace": "workloads"}),
    ("apply_network_policy", {"pod": "nginx-x", "namespace": "workloads"}),
    ("kill_pod", {"pod": "nginx-x", "namespace": "workloads"}),
    ("quarantine_pod", {"pod": "nginx-x", "namespace": "workloads"}),
    ("capture_forensics", {"pod": "nginx-x", "namespace": "workloads"}),
    ("page_security", {"reason": "reverse shell detected"}),
]


@pytest.mark.parametrize(
    ("tool", "args"), SECURITY_TOOL_CASES, ids=[c[0] for c in SECURITY_TOOL_CASES]
)
@pytest.mark.asyncio
async def test_security_tool_allow_path_active_mode(
    tool: str,
    args: dict[str, Any],
    stub_mode,
    stub_opa,
    stub_security_tools,
    stub_audit,
) -> None:
    """In active mode + OPA-allow, each security tool dispatches + returns
    success through MCP."""
    stub_mode["mode"] = "active"
    res = await _call(tool, args)
    assert res.is_error is False
    payload = json.loads(res.content[0].text) if res.content else {}
    assert payload.get("success") is True
    assert stub_security_tools.get(tool), f"{tool} was not dispatched"


@pytest.mark.parametrize(
    ("tool", "args"), SECURITY_TOOL_CASES, ids=[c[0] for c in SECURITY_TOOL_CASES]
)
@pytest.mark.asyncio
async def test_security_tool_deny_in_observe_only(
    tool: str,
    args: dict[str, Any],
    stub_mode,
    stub_opa,
    stub_security_tools,
    stub_audit,
) -> None:
    """observe-only denies EVERY tool at the MCP gate — the OPA mode-gate is
    global (it overrides the matrix), so even page_security is denied via the
    direct MCP path. (In the graph, escalation runs page_security through
    node_escalate, which bypasses this gate.)"""
    stub_mode["mode"] = "observe-only"
    res = await _call(tool, args)
    assert res.is_error is True
