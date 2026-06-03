"""Hermetic tests for the Phase 14.5 scene + forecasts endpoints.

`_scene_from_incident` is the pure projection that turns one
persisted IncidentState into the WorkloadScene payload the SPA
uses to render the "scene" pane on the use-case-launch view.

We drive it directly with synthetic incident dicts — no FastAPI
TestClient needed for the projection logic. Endpoint-level
integration is exercised by the existing tests/test_api.py
infrastructure.
"""

from __future__ import annotations

import time

from agent.api import _scene_from_incident
from agent.baselines.store import BaselineFit, BaselineStore, MetricKey, Sample
from agent.incident_store import filter_and_paginate

# ─── _scene_from_incident — outcome verdicts ─────────────────────────────


def _base_incident(**overrides) -> dict:
    """Build a synthetic IncidentState matching the real graph shape.

    Note: per-step data lives under SEMANTIC keys (`diagnosis`,
    `proposed_action`, `policy_decision`, `action_result`,
    `verification`) — NOT under node names. The scene projection's
    `step_order` maps node-name → semantic-key. Tests that override
    individual sections use the semantic-key name; the test reader
    can pass e.g. `verification={"healed": False}` to mean "verify
    step reported not healed."""
    base = {
        "incident_id": "inc-xyz",
        "namespace": "workloads",
        "pod": "nginx-demo-7b8d",
        "severity": "high",
        "rule": "memory-leak",
        "agent_mode": "active",
        "saved_at": "2026-06-03T18:00:00Z",
        "started_at": "2026-06-03T17:59:00Z",
        "duration_seconds": 60.0,
        "phase": "Running",
        "context": {"runbook_hits": [{"slug": "001-memory-leak", "score": 0.9}]},
        "k8sgpt_findings": [],
        "baselines": [],
        "diagnosis": {
            "summary": "Pod is OOM-leaking under load.",
            "root_cause": "Bad cache eviction.",
            "confidence": 0.84,
        },
        "proposed_action": {
            "tool": "restart_pod",
            "args": {"pod_name": "nginx-demo-7b8d", "namespace": "workloads"},
            "rationale": "Clear leaked state.",
        },
        "policy_decision": {"allow": True, "reasons": []},
        "action_result": {"tool": "restart_pod", "success": True},
        "verification": {"healed": True},
    }
    base.update(overrides)
    return base


def test_scene_healed_when_verify_says_healed() -> None:
    scene = _scene_from_incident(_base_incident())
    assert scene.outcome.verdict == "healed"
    assert "verify confirmed" in scene.outcome.reason


def test_scene_failed_when_verify_says_not_healed() -> None:
    incident = _base_incident(verification={"healed": False})
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "failed"
    assert "did NOT recover" in scene.outcome.reason


def test_scene_escalated_carries_explicit_reason() -> None:
    incident = _base_incident(
        verification={"healed": None},
        escalated=True,
        escalation_reason="diagnosis confidence 0.42 below per-rule threshold",
    )
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "escalated"
    assert "0.42 below" in scene.outcome.reason


def test_scene_escalated_falls_back_to_policy_reasons() -> None:
    incident = _base_incident(
        verification={"healed": None},
        escalated=True,
        policy_decision={"allow": False, "reasons": ["L3 matrix denial", "kube-system protected"]},
    )
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "escalated"
    assert "matrix denial" in scene.outcome.reason
    assert "protected" in scene.outcome.reason


def test_scene_observe_only_when_mode_not_active() -> None:
    incident = _base_incident(
        agent_mode="observe-only",
        verification={"healed": None},
        action_result={},
    )
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "observe_only"
    assert "observe-only" in scene.outcome.reason


def test_scene_unknown_when_terminal_classification_missing() -> None:
    incident = _base_incident(verification={"healed": None}, escalated=False)
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "unknown"


def test_scene_failed_when_execute_returned_error_even_with_verify_none() -> None:
    """Regression guard for the bug seen in the Phase 14.5a demo screenshot:
    `tune_hpa` execute failed with `kubectl exit 1`, verify never ran,
    `escalated=True`. The OLD code surfaced verdict="escalated" + the
    useless "Escalated without explicit reason." string. The verdict
    is actually `failed` (the action did not succeed) and the reason
    should carry the kubectl error so the operator doesn't have to
    dig through the audit chain."""
    incident = _base_incident(
        verification={"healed": None},
        proposed_action={
            "tool": "tune_hpa",
            "args": {"hpa_name": "k3d-poc-server", "namespace": "workloads", "max_replicas": 5},
            "rationale": "forecasted surge",
        },
        action_result={"tool": "tune_hpa", "success": False, "error": "kubectl exit 1"},
        escalated=True,
    )
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "failed"
    assert "tune_hpa" in scene.outcome.reason
    assert "kubectl exit 1" in scene.outcome.reason


def test_scene_failed_when_execute_stderr_present_even_without_error() -> None:
    """Some tools surface failure as a non-empty `stderr` rather than
    `error` (e.g. a kubectl exit 0 with warnings). Treat that as a
    failure too — the operator should see what went wrong."""
    incident = _base_incident(
        verification={"healed": None},
        action_result={
            "tool": "set_resources",
            "success": False,
            "stderr": "Error from server (Conflict): operation cannot be fulfilled",
        },
    )
    scene = _scene_from_incident(incident)
    assert scene.outcome.verdict == "failed"
    assert "Conflict" in scene.outcome.reason


def test_scene_pod_state_carries_tool_name_when_execute_failed() -> None:
    """The POD state field should not be `?` when we have action_result
    data — that was the second bug in the demo screenshot. With a
    failed action, the row state should read `(tune_hpa failed)`."""
    incident = _base_incident(
        verification={"healed": None},
        proposed_action={
            "tool": "tune_hpa",
            "args": {"hpa_name": "k3d-poc-server", "namespace": "workloads"},
            "rationale": "forecasted surge",
        },
        action_result={"tool": "tune_hpa", "success": False, "error": "kubectl exit 1"},
        escalated=True,
    )
    scene = _scene_from_incident(incident)
    pod_row = next((r for r in scene.resources if r.kind == "Pod"), None)
    assert pod_row is not None
    assert pod_row.state == "(tune_hpa failed)"
    assert pod_row.outcome == "failed"


def test_scene_outcome_carries_metadata() -> None:
    scene = _scene_from_incident(_base_incident())
    assert scene.outcome.confidence == 0.84
    assert scene.outcome.rule == "memory-leak"
    assert scene.outcome.tool == "restart_pod"
    assert scene.outcome.time_to_outcome_seconds == 60.0


# ─── _scene_from_incident — resources affected ───────────────────────────


def test_scene_resources_include_target_pod_with_reconciled_outcome() -> None:
    scene = _scene_from_incident(_base_incident())
    pod_row = next((r for r in scene.resources if r.kind == "Pod"), None)
    assert pod_row is not None
    assert pod_row.name == "nginx-demo-7b8d"
    assert pod_row.namespace == "workloads"
    assert pod_row.outcome == "reconciled"


def test_scene_resources_mark_paged_outcome_on_escalation() -> None:
    incident = _base_incident(
        verification={"healed": None},
        escalated=True,
        escalation_reason="page_human",
    )
    scene = _scene_from_incident(incident)
    pod_row = next((r for r in scene.resources if r.kind == "Pod"), None)
    assert pod_row is not None
    assert pod_row.outcome == "paged"


def test_scene_resources_synthesize_deployment_row_for_rollout_undo() -> None:
    incident = _base_incident(
        proposed_action={
            "tool": "rollout_undo",
            "args": {"deployment_name": "nginx-demo", "namespace": "workloads"},
            "rationale": "regression",
        },
        action_result={"tool": "rollout_undo", "success": True},
    )
    scene = _scene_from_incident(incident)
    deploy_row = next((r for r in scene.resources if r.kind == "Deployment"), None)
    assert deploy_row is not None
    assert deploy_row.name == "nginx-demo"
    assert "target of rollout_undo" in deploy_row.state


def test_scene_resources_synthesize_node_row_for_drain_node() -> None:
    incident = _base_incident(
        pod=None,
        proposed_action={
            "tool": "drain_node",
            "args": {"node_name": "k3d-poc-server-0", "grace_period_s": 300},
            "rationale": "node decay",
        },
        action_result={"tool": "drain_node", "success": True},
    )
    scene = _scene_from_incident(incident)
    node_row = next((r for r in scene.resources if r.kind == "Node"), None)
    assert node_row is not None
    assert node_row.name == "k3d-poc-server-0"


def test_scene_resources_synthesize_hpa_row_for_tune_hpa() -> None:
    """Phase 14 — tune_hpa carries `hpa_name` in args, not pod_name."""
    incident = _base_incident(
        proposed_action={
            "tool": "tune_hpa",
            "args": {"hpa_name": "checkout-hpa", "namespace": "workloads", "max_replicas": 10},
            "rationale": "forecast surge",
        },
        action_result={"tool": "tune_hpa", "success": True},
    )
    scene = _scene_from_incident(incident)
    hpa_row = next((r for r in scene.resources if r.kind == "HorizontalPodAutoscaler"), None)
    assert hpa_row is not None
    assert hpa_row.name == "checkout-hpa"


# ─── _scene_from_incident — action chain ─────────────────────────────────


def test_scene_steps_walk_the_graph_topology() -> None:
    scene = _scene_from_incident(_base_incident())
    names = [s.name for s in scene.steps]
    # Order MUST match the graph topology so the SPA stepper looks right.
    expected = [
        "triage",
        "retrieve_context",
        "k8sgpt_analyze",
        "enrich_with_baselines",
        "diagnose",
        "plan_action",
        "policy_gate",
        "execute",
        "verify",
    ]
    assert names == expected


def test_scene_steps_skip_missing_sections() -> None:
    """Stale-resolved incidents short-circuit before most nodes —
    the scene should reflect that, not invent steps. Setting a
    semantic section to `None` (instead of omitting) verifies the
    `isinstance(raw_section, dict)` guard."""
    incident = _base_incident(
        context=None,
        k8sgpt_findings=None,
        baselines=None,
        diagnosis=None,
        proposed_action=None,
        policy_decision=None,
        action_result=None,
        verification=None,
    )
    scene = _scene_from_incident(incident)
    assert [s.name for s in scene.steps] == ["triage"]


def test_scene_steps_flag_low_confidence_as_warn() -> None:
    incident = _base_incident(diagnosis={"summary": "uncertain", "confidence": 0.3})
    scene = _scene_from_incident(incident)
    diagnose = next(s for s in scene.steps if s.name == "diagnose")
    assert diagnose.status == "warn"


def test_scene_steps_flag_opa_deny_as_warn() -> None:
    incident = _base_incident(
        policy_decision={"allow": False, "reasons": ["L3 matrix denial"]},
    )
    scene = _scene_from_incident(incident)
    gate = next(s for s in scene.steps if s.name == "policy_gate")
    assert gate.status == "warn"
    assert "deny" in gate.summary
    assert "L3 matrix denial" in gate.summary


def test_scene_steps_flag_execute_failure_as_error() -> None:
    incident = _base_incident(action_result={"tool": "restart_pod", "error": "kubectl exit 1"})
    scene = _scene_from_incident(incident)
    exe = next(s for s in scene.steps if s.name == "execute")
    assert exe.status == "error"


# ─── incident_store.filter_and_paginate — Phase 14.5 filters ─────────────


def test_filter_by_pod_returns_only_matching_incidents() -> None:
    items = [
        _base_incident(incident_id="a", pod="alpha"),
        _base_incident(incident_id="b", pod="beta"),
        _base_incident(incident_id="c", pod="alpha"),
    ]
    total, page = filter_and_paginate(items, pod="alpha")
    assert total == 2
    assert {p["incident_id"] for p in page} == {"a", "c"}


def test_filter_by_rule_returns_only_matching_incidents() -> None:
    items = [
        _base_incident(incident_id="a", rule="memory-leak"),
        _base_incident(incident_id="b", rule="crashloop"),
    ]
    total, page = filter_and_paginate(items, rule="memory-leak")
    assert total == 1
    assert page[0]["incident_id"] == "a"


def test_filter_combines_pod_and_namespace() -> None:
    items = [
        _base_incident(incident_id="a", namespace="workloads", pod="alpha"),
        _base_incident(incident_id="b", namespace="default", pod="alpha"),
    ]
    total, page = filter_and_paginate(items, namespace="workloads", pod="alpha")
    assert total == 1
    assert page[0]["incident_id"] == "a"


# ─── /api/forecasts — sort + level fallback ──────────────────────────────


def test_forecasts_endpoint_sorts_anomalous_first(tmp_path, monkeypatch) -> None:
    """The endpoint is wired to the singleton store; for this test we
    swap in a tmp store + drive `list_forecasts` directly."""
    from agent.api import list_forecasts
    from agent.baselines import snapshot as snap_mod

    store = BaselineStore(tmp_path / "baselines.db")
    # Climbing key — should classify as warning/critical.
    climbing = MetricKey("container_memory_rss_bytes", "workloads", "leaker")
    now = time.time()
    store.insert_samples(
        [Sample(key=climbing, ts=now - (60 - i), value=100.0 + i * 2.0) for i in range(60)]
    )
    store.upsert_fit(
        BaselineFit(
            key=climbing,
            mean=100.0,
            std=5.0,
            n_samples=60,
            hour_medians=[None] * 24,
            computed_at=now,
        )
    )
    # Stationary key — should classify as safe/none.
    calm = MetricKey("container_cpu_rate_5m", "workloads", "calm")
    store.insert_samples([Sample(key=calm, ts=now - (60 - i), value=0.5) for i in range(60)])
    store.upsert_fit(
        BaselineFit(
            key=calm,
            mean=0.5,
            std=0.05,
            n_samples=60,
            hour_medians=[None] * 24,
            computed_at=now,
        )
    )
    monkeypatch.setattr("agent.api.get_default_store", lambda: store, raising=False)
    monkeypatch.setattr(snap_mod, "get_default_store", lambda: store)
    # Also patch the agent.baselines module re-export the endpoint imports.
    import agent.baselines as bl

    monkeypatch.setattr(bl, "get_default_store", lambda: store)
    result = list_forecasts(ns=None, pod=None)
    assert result.total == 2
    # Anomalous one should be FIRST regardless of insertion order.
    assert result.items[0].metric == "container_memory_rss_bytes"
    assert result.items[0].level in {"warning", "critical"}
    assert result.items[1].metric == "container_cpu_rate_5m"
