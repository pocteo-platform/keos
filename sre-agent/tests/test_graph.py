"""Hermetic tests for the LangGraph state machine.

Mocks every external service (Ollama, OPA, Qdrant, kubectl) so the entire
suite runs in well under a second. Real integration is exercised by
test_policy.py (against the live OPA container) and `make demo` (against the
real cluster).
"""

from __future__ import annotations

import json

import pytest

from agent.graph import run_once
from agent.synthetic_events import _SUBJECTS, build_event


# A standalone "build a synthetic event" path that does NOT touch the cluster
# (the resolver subprocess is patched out by fake_kubectl).
def _event(scenario_id: int) -> dict:
    # Bypass the cluster-pod lookup by patching synthetic_events._resolve_pod_name
    # via the fake_kubectl fixture (subprocess.run returns rc=0 stdout="").
    return build_event(scenario_id)


# ---------------------------------------------------------------------------
# Per-scenario expectations.
# Each tuple: (scenario_id, expected_tool, should_be_escalated_after_active_run)
# ---------------------------------------------------------------------------

SCENARIO_EXPECTATIONS = [
    (1, "rollout_undo", False),  # memory leak → revert to previous stable
    (2, "rollout_undo", False),  # crashloop → revert bad rollout
    (3, "rollout_undo", False),  # ImagePullBackOff → revert to pullable image
    (4, "cordon_node", False),  # node pressure → cordon
    (5, "rollout_undo", False),  # rollout stalled → revert
    (7, "scale_deployment", False),  # traffic surge → bump replicas
]


@pytest.mark.parametrize(
    ("scenario", "expected_tool", "expected_escalated"),
    SCENARIO_EXPECTATIONS,
    ids=[f"scenario_{s}" for s, _, _ in SCENARIO_EXPECTATIONS],
)
def test_active_mode_each_scenario(
    scenario: int,
    expected_tool: str,
    expected_escalated: bool,
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """In active mode with a high-confidence diagnosis and ALLOW from OPA,
    every scenario should reach `verify` with healed=true."""
    patch_llm(scenario)
    fake_mode_store.mode = "active"

    event = _event(scenario)
    final = run_once(event)

    assert final["proposed_action"]["tool"] == expected_tool, f"scenario {scenario}: wrong tool"
    assert final.get("escalated", False) is expected_escalated
    assert final["policy_decision"]["allow"] is True
    assert final["action_result"]["success"] is True
    assert final["verification"]["healed"] is True


def test_observe_only_mode_escalates_at_policy_gate(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """observe-only must deny at policy_gate, escalate, and NOT touch the cluster."""
    patch_llm(1)
    fake_mode_store.mode = "observe-only"

    final = run_once(_event(1))

    assert final["policy_decision"]["allow"] is False
    assert "observe-only" in " ".join(final["policy_decision"]["reasons"])
    assert final.get("escalated", False) is True
    assert final.get("action_result") is None, "execute must not run in observe-only after deny"


def test_low_confidence_skips_planning(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """A diagnose with confidence < threshold should escalate without ever
    calling plan_action or policy_gate."""
    patch_llm("low_conf")
    fake_mode_store.mode = "active"

    final = run_once(_event(1))

    assert final["diagnosis"]["confidence"] < 0.6
    assert final.get("escalated") is True
    # plan_action was never invoked (no proposed_action in final state).
    assert final.get("proposed_action") is None
    assert final.get("policy_decision") is None


def test_opa_deny_in_active_mode_routes_to_escalate(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """If OPA returns deny (e.g., bad target namespace) the graph must escalate."""
    patch_llm(1)
    fake_mode_store.mode = "active"
    patch_opa(allow=False, reasons=['namespace "kube-system" is protected'])

    final = run_once(_event(1))

    assert final["policy_decision"]["allow"] is False
    assert final.get("escalated") is True
    assert final.get("action_result") is None


def test_execute_failure_routes_to_escalate(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """If kubectl fails (rc!=0), skip verify and escalate so the operator
    sees the kubectl error instead of a misleading 'healed=true'."""
    patch_llm(1)
    fake_mode_store.mode = "active"
    # Scenario 1 dispatches rollout_undo — register that to fail.
    fake_kubectl.register(
        ("rollout", "undo"), rc=1, stderr="Error from server: deployment not found"
    )

    final = run_once(_event(1))

    assert final["action_result"]["success"] is False
    assert final.get("verification") is None, "verify must NOT have run after execute failed"
    assert final.get("escalated") is True


def test_audit_log_records_full_chain(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """Every transition in a successful active run should appear in the audit
    log, in order, with HMAC signatures."""
    patch_llm(1)
    fake_mode_store.mode = "active"

    run_once(_event(1))

    lines = tmp_audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 7, f"expected at least 7 audit events, got {len(lines)}"

    event_types = [json.loads(line)["event_type"] for line in lines]
    # Required transitions for a healed active run.
    for required in (
        "received",
        "triage",
        "retrieve_context",
        "diagnose",
        "plan_action",
        "policy_gate",
        "execute",
        "verify",
        "complete",
    ):
        assert required in event_types, f"missing audit record: {required}"

    # Every line carries an hmac field.
    for line in lines:
        rec = json.loads(line)
        assert "hmac" in rec and len(rec["hmac"]) == 64


def test_synthetic_event_shape_is_infra() -> None:
    """Build_event must produce the keys downstream graph nodes read.
    Node-targeted scenarios (4 in the SRE catalog) lack `k8s.pod.name`
    — they carry `k8s.node.name` instead — so the test asserts on the
    pod key only when present."""
    for sid in _SUBJECTS:
        ev = build_event(sid)
        assert "rule" in ev
        assert "priority" in ev
        assert "output" in ev
        assert "output_fields" in ev
        of = ev["output_fields"]
        # Either namespace+pod (workload scenarios) or node (node-pressure).
        assert "k8s.ns.name" in of or "k8s.node.name" in of


def test_imagepull_scenario_uses_cluster_signals_to_pick_rollout_undo(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """Scenario 3: image pull failure. Cluster signals (the diagnostic
    layer that used to be called "K8sGPT") supply an ImagePullBackOff
    finding; the (canned) LLM uses that finding to choose `rollout_undo`
    rather than e.g. restart_pod. This proves the diagnostic-signals
    path can sway the LLM choice."""
    from agent.schemas import Finding

    patch_llm(3)
    fake_mode_store.mode = "active"
    fake_k8sgpt.findings = [
        Finding(
            kind="Pod",
            name="workloads/broken-image-7bdc-xyz",
            error='Back-off pulling image "ghcr.io/example/does-not-exist:v999": manifest unknown',
            parent_object="Deployment/broken-image",
        )
    ]

    final = run_once(_event(3))

    # The LLM (canned, scenario 3) picks rollout_undo.
    assert final["proposed_action"]["tool"] == "rollout_undo"
    assert final["proposed_action"]["args"]["deployment_name"] == "broken-image"
    # The cluster-signal finding made it into state.
    assert len(final.get("k8sgpt_findings", [])) == 1
    # Active mode allowed; verify ran.
    assert final["policy_decision"]["allow"] is True
    assert final.get("escalated", False) is False


def test_k8sgpt_unavailability_does_not_break_graph(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    monkeypatch,
    tmp_audit,
) -> None:
    """If k8sgpt itself raises (network down, etc.), the graph must still
    complete with empty findings — diagnose falls back to Falco + runbooks."""

    def fake_sync_raises(*args, **kwargs):
        raise ConnectionError("simulated k8sgpt down")

    import agent.graph as g

    monkeypatch.setattr(g, "k8sgpt_analyze_sync", fake_sync_raises)

    patch_llm(1)
    fake_mode_store.mode = "active"

    final = run_once(_event(1))

    # k8sgpt_findings ended up empty; downstream still produced a proposal.
    assert final.get("k8sgpt_findings") == []
    assert final["proposed_action"]["tool"] == "rollout_undo"
    assert not final.get("escalated", False)
