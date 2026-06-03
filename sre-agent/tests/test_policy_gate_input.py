"""Unit tests for `agent.policy_gate._target_for` — the helper that
shapes each ProposedAction into the `{namespace, kind, name}` triple
OPA's policy contract expects.

No live OPA. Pure-function tests over the input-builder. These guard
the corners exposed by the Phase 9.2 SRE action catalog (cordon_node,
drain_node, uncordon_node) — before the Phase-13 follow-up fix, every
node-scoped action emitted `target.kind="Unknown"` and `target.name="n/a"`
in the audit log, even when the action's args carried the node name.
"""

from __future__ import annotations

from agent.policy_gate import _target_for
from agent.schemas import ProposedAction


def _action(tool: str, scope: str, args: dict) -> ProposedAction:
    """Builds a ProposedAction skipping the validation we don't need
    for these tests. `rationale` is required by the schema so we
    provide a placeholder."""
    return ProposedAction(
        tool=tool,
        args=args,
        scope=scope,
        reversible=True,
        rationale="test fixture",
    )


# ─── Pod-scoped actions ─────────────────────────────────────────────────


def test_target_for_restart_pod_extracts_pod_name() -> None:
    action = _action(
        "restart_pod",
        "pod",
        {"pod_name": "nginx-demo-7b8d", "namespace": "workloads"},
    )
    t = _target_for(action, namespace="workloads")
    assert t == {"namespace": "workloads", "kind": "Pod", "name": "nginx-demo-7b8d"}


def test_target_for_pod_action_falls_back_to_state_namespace() -> None:
    """If the action's args don't carry `namespace`, fall back to the
    incident-level namespace passed by the graph."""
    action = _action("restart_pod", "pod", {"pod_name": "nginx-demo-7b8d"})
    t = _target_for(action, namespace="workloads")
    assert t["namespace"] == "workloads"


# ─── Deployment-scoped actions ──────────────────────────────────────────


def test_target_for_rollout_undo_extracts_deployment_name() -> None:
    action = _action(
        "rollout_undo",
        "deploy",
        {"deployment_name": "nginx-demo", "namespace": "workloads"},
    )
    t = _target_for(action, namespace="workloads")
    assert t == {
        "namespace": "workloads",
        "kind": "Deployment",
        "name": "nginx-demo",
    }


# ─── Node-scoped actions (Phase 9.2 catalog + Phase 13 follow-up) ───────


def test_target_for_drain_node_extracts_node_name() -> None:
    """Regression guard. Before the fix, drain_node's `node_name` arg
    wasn't checked — target.name fell through to "n/a"."""
    action = _action("drain_node", "node", {"node_name": "k3d-poc-server-0"})
    t = _target_for(action, namespace="workloads")
    assert t["name"] == "k3d-poc-server-0"
    assert t["kind"] == "Node"


def test_target_for_cordon_node_extracts_node_name() -> None:
    action = _action("cordon_node", "node", {"node_name": "edge-1"})
    t = _target_for(action, namespace=None)
    assert t["kind"] == "Node"
    assert t["name"] == "edge-1"


def test_target_for_uncordon_node_extracts_node_name() -> None:
    action = _action("uncordon_node", "node", {"node_name": "edge-1"})
    t = _target_for(action, namespace=None)
    assert t["kind"] == "Node"
    assert t["name"] == "edge-1"


def test_target_for_node_action_clears_namespace() -> None:
    """Node-scoped actions don't carry a namespace — the audit log
    should not show one. The Rego `protected_namespaces` set tests
    `target.namespace`, and an empty string is intentionally NOT in
    that set so node actions pass the gate."""
    action = _action("drain_node", "node", {"node_name": "k3d-poc-server-0"})
    t = _target_for(action, namespace="workloads")  # incident ns ignored
    assert t["namespace"] == ""


# ─── page_human / n/a scope ─────────────────────────────────────────────


def test_target_for_page_human_has_none_kind() -> None:
    action = _action("page_human", "n/a", {"reason": "needs human eyes"})
    t = _target_for(action, namespace=None)
    assert t["kind"] == "None"
    # No name was provided; the helper falls through to "n/a".
    assert t["name"] == "n/a"


# ─── Arg-key precedence ─────────────────────────────────────────────────


def test_target_for_arg_key_precedence_pod_beats_node() -> None:
    """A pod-scoped action with BOTH pod_name and node_name in args
    (operator slip, or a richly-decorated use case) should still
    prefer pod_name — matches the documented arg-search order."""
    action = _action(
        "restart_pod",
        "pod",
        {"pod_name": "real-pod", "node_name": "some-node"},
    )
    t = _target_for(action, namespace="workloads")
    assert t["name"] == "real-pod"
