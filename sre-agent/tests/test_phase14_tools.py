"""Hermetic tests for Phase 14's three new action tools.

`agent.tools.tune_hpa`, `set_resources`, `evict_workload` each shell
out to `kubectl` in the real path. These tests don't run kubectl —
they exercise dry-run mode and the input-validation guards, which
are pure-Python and don't touch a live cluster.
"""

from __future__ import annotations

from agent.tools import evict_workload, set_resources, tune_hpa

# ─── tune_hpa ──────────────────────────────────────────────────────────


def test_tune_hpa_rejects_call_with_no_knobs() -> None:
    """The agent shouldn't ever call tune_hpa without specifying at
    least one of min/max/target — silent no-op would be confusing in
    the audit log."""
    result = tune_hpa(hpa_name="checkout", namespace="workloads")
    assert not result.success
    assert "min_replicas" in (result.error or "")


def test_tune_hpa_dry_run_returns_planned_patch() -> None:
    result = tune_hpa(
        hpa_name="checkout",
        namespace="workloads",
        min_replicas=2,
        max_replicas=10,
        target_cpu=70,
        dry_run=True,
    )
    assert result.success
    assert result.dry_run
    assert "kubectl patch hpa/checkout" in result.stdout
    assert result.args == {
        "hpa_name": "checkout",
        "namespace": "workloads",
        "min_replicas": 2,
        "max_replicas": 10,
        "target_cpu": 70,
    }


def test_tune_hpa_dry_run_with_only_max_replicas() -> None:
    """Each knob is independently optional. Min-replicas not set →
    not included in the patch."""
    result = tune_hpa(hpa_name="checkout", namespace="workloads", max_replicas=20, dry_run=True)
    assert result.success
    assert result.dry_run
    # min_replicas + target_cpu fields preserved in args as None.
    assert result.args["min_replicas"] is None
    assert result.args["target_cpu"] is None
    assert result.args["max_replicas"] == 20


# ─── set_resources ─────────────────────────────────────────────────────


def test_set_resources_rejects_call_with_no_requests_or_limits() -> None:
    result = set_resources(
        deployment_name="checkout",
        namespace="workloads",
        container="api",
    )
    assert not result.success
    assert "requests" in (result.error or "")


def test_set_resources_dry_run_returns_planned_patch() -> None:
    result = set_resources(
        deployment_name="checkout",
        namespace="workloads",
        container="api",
        requests={"memory": "256Mi"},
        limits={"memory": "1Gi"},
        dry_run=True,
    )
    assert result.success
    assert result.dry_run
    assert "kubectl patch deploy/checkout" in result.stdout
    assert result.args["requests"] == {"memory": "256Mi"}
    assert result.args["limits"] == {"memory": "1Gi"}


def test_set_resources_accepts_requests_only() -> None:
    """Forecasted rightsizing usually moves only `requests` down —
    `limits` stays where it is to avoid pulling capacity from
    in-flight workloads."""
    result = set_resources(
        deployment_name="checkout",
        namespace="workloads",
        container="api",
        requests={"memory": "128Mi"},
        dry_run=True,
    )
    assert result.success
    assert result.args["limits"] is None


# ─── evict_workload ────────────────────────────────────────────────────


def test_evict_workload_rejects_empty_selector() -> None:
    """Empty selector would delete every pod in the namespace — the
    agent must NEVER do that accidentally."""
    result = evict_workload(namespace="workloads", label_selector="")
    assert not result.success
    assert "label_selector" in (result.error or "")


def test_evict_workload_rejects_whitespace_only_selector() -> None:
    result = evict_workload(namespace="workloads", label_selector="   ")
    assert not result.success
    assert "label_selector" in (result.error or "")


def test_evict_workload_dry_run_returns_planned_delete() -> None:
    result = evict_workload(namespace="workloads", label_selector="app=checkout", dry_run=True)
    assert result.success
    assert result.dry_run
    assert "kubectl delete pods" in result.stdout
    assert "app=checkout" in result.stdout


# ─── Policy gate / target shaping ──────────────────────────────────────


def test_policy_gate_extracts_hpa_name() -> None:
    """`_target_for` (Phase 13 follow-up) added `hpa_name` to the
    arg-search order. Verify the audit shape for tune_hpa carries
    the HPA name, not "n/a"."""
    from agent.policy_gate import _target_for
    from agent.schemas import ProposedAction

    action = ProposedAction(
        tool="tune_hpa",
        args={
            "hpa_name": "checkout-hpa",
            "namespace": "workloads",
            "max_replicas": 10,
        },
        scope="deploy",
        reversible=True,
        rationale="test fixture",
    )
    t = _target_for(action, namespace="workloads")
    assert t["name"] == "checkout-hpa"
    assert t["kind"] == "Deployment"
    assert t["namespace"] == "workloads"


def test_policy_gate_extracts_label_selector_as_name() -> None:
    """evict_workload doesn't have a single object name — using the
    label selector as the audit `target.name` is the most useful
    surface for the operator (it's what was actually targeted)."""
    from agent.policy_gate import _target_for
    from agent.schemas import ProposedAction

    action = ProposedAction(
        tool="evict_workload",
        args={"namespace": "workloads", "label_selector": "app=checkout"},
        scope="ns",
        reversible=True,
        rationale="test fixture",
    )
    t = _target_for(action, namespace="workloads")
    assert t["name"] == "app=checkout"
    assert t["kind"] == "Namespace"
