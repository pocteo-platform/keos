"""Black-box tests for policies/agent.rego, driven via OPA's HTTP API.

These tests require the `opa` service from docker-compose to be up. The agent's
build pipeline does `make up` before `make test-policy`, but if you run pytest
directly: `docker compose up -d opa` first.
"""

from __future__ import annotations

import os

import httpx
import pytest

OPA_URL = os.environ.get("OPA_URL", "http://localhost:8181")
QUERY = f"{OPA_URL}/v1/data/agent/authz"


def _evaluate(payload: dict) -> dict:
    """POST the input doc to OPA, return the decision sub-document.

    Raises a clear error if OPA isn't reachable so a failing precondition
    doesn't masquerade as a policy bug.
    """
    try:
        resp = httpx.post(QUERY, json={"input": payload}, timeout=5.0)
    except httpx.ConnectError as exc:
        pytest.fail(f"OPA not reachable at {OPA_URL}. Run `make up` first. ({exc})")
    resp.raise_for_status()
    return resp.json()["result"]


def _target(namespace: str = "workloads", kind: str = "Pod", name: str = "x") -> dict:
    return {"namespace": namespace, "kind": kind, "name": name}


# ---------------------------------------------------------------------------
# Parametrised happy-path + denial cases. Each row encodes the prompt's matrix.
#
#   id                          : human-readable case id
#   input                       : full payload for OPA
#   expect_allow                : True/False
#   reason_contains             : substring expected in at least one reason
#                                 (None when expect_allow is True)
# ---------------------------------------------------------------------------
CASES: list[tuple[str, dict, bool, str | None]] = [
    # 1 — restart_pod L2 (high) in workloads + active → allow
    (
        "restart_pod_high_active_workloads",
        {"action": "restart_pod", "severity": "high", "agent_mode": "active", "target": _target()},
        True,
        None,
    ),
    # 2 — restart_pod L2 but observe-only mode → deny (mode gate)
    (
        "restart_pod_blocked_in_observe_only",
        {
            "action": "restart_pod",
            "severity": "high",
            "agent_mode": "observe-only",
            "target": _target(),
        },
        False,
        "agent_mode",
    ),
    # 3 — scale_deployment at critical to kube-system → deny (protected ns)
    (
        "scale_kube_system_blocked_even_at_critical",
        {
            "action": "scale_deployment",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(namespace="kube-system", kind="Deployment", name="coredns"),
        },
        False,
        "protected",
    ),
    # 4 — rollout_undo at low → L3 → deny (matrix gate)
    (
        "rollout_undo_low_severity_blocked",
        {
            "action": "rollout_undo",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="nginx-demo"),
        },
        False,
        "L3",
    ),
    # 5 — rollout_undo at high → L1 → allow
    (
        "rollout_undo_high_allowed",
        {
            "action": "rollout_undo",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="nginx-demo"),
        },
        True,
        None,
    ),
    # 6 — cordon_node at high → L1 → allow
    (
        "cordon_node_high_allowed",
        {
            "action": "cordon_node",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(namespace="", kind="Node", name="k3d-poc-server-0"),
        },
        True,
        None,
    ),
    # 7 — cordon_node at low → L3 → deny
    (
        "cordon_node_low_blocked",
        {
            "action": "cordon_node",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(namespace="", kind="Node", name="k3d-poc-server-0"),
        },
        False,
        "L3",
    ),
    # 8 — page_human in observe-only → deny (mode is the global gate;
    # page_human is L1 in the matrix but mode wins).
    (
        "page_human_blocked_in_observe_only",
        {
            "action": "page_human",
            "severity": "low",
            "agent_mode": "observe-only",
            "target": _target(namespace="workloads", kind="None", name="n/a"),
        },
        False,
        "agent_mode",
    ),
    # 9 — page_human in active → allow.
    (
        "page_human_active_allowed",
        {
            "action": "page_human",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(namespace="workloads", kind="None", name="n/a"),
        },
        True,
        None,
    ),
    # 10 — unknown action → deny.
    (
        "unknown_action_blocked",
        {
            "action": "nuke_cluster",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(),
        },
        False,
        "unknown action",
    ),
    # 11 — drain_node at low → L4 → deny (highest barrier).
    (
        "drain_node_low_blocked",
        {
            "action": "drain_node",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(namespace="", kind="Node", name="k3d-poc-server-0"),
        },
        False,
        "L4",
    ),
    # 12 — frozen mode blocks even the lowest-risk action.
    (
        "frozen_mode_blocks_page_human",
        {"action": "page_human", "severity": "low", "agent_mode": "frozen", "target": _target()},
        False,
        "agent_mode",
    ),
    # 13 — Unknown severity rejected even for a known action.
    (
        "unknown_severity_blocked",
        {
            "action": "restart_pod",
            "severity": "catastrophic",
            "agent_mode": "active",
            "target": _target(),
        },
        False,
        "unknown severity",
    ),
    # 14 — scale_deployment at low (L2) in workloads + active → allow.
    (
        "scale_low_workloads_allowed",
        {
            "action": "scale_deployment",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="nginx-demo"),
        },
        True,
        None,
    ),
    # 15 — drain_node at critical → L1 → allow.
    (
        "drain_node_critical_allowed",
        {
            "action": "drain_node",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(namespace="", kind="Node", name="k3d-poc-server-0"),
        },
        True,
        None,
    ),
    # 16 — Phase 14: tune_hpa at high → L1 → allow.
    (
        "tune_hpa_high_allowed",
        {
            "action": "tune_hpa",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="checkout-hpa"),
        },
        True,
        None,
    ),
    # 17 — Phase 14: tune_hpa at low → L3 → deny (operator approval).
    (
        "tune_hpa_low_blocked",
        {
            "action": "tune_hpa",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="checkout-hpa"),
        },
        False,
        "L3",
    ),
    # 18 — Phase 14: set_resources at critical → L1 → allow.
    (
        "set_resources_critical_allowed",
        {
            "action": "set_resources",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="checkout-api"),
        },
        True,
        None,
    ),
    # 19 — Phase 14: evict_workload at low → L4 → deny.
    (
        "evict_workload_low_blocked",
        {
            "action": "evict_workload",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(kind="Namespace", name="app=checkout"),
        },
        False,
        "L4",
    ),
    # 20 — Phase 14: evict_workload at high → L2 → allow.
    (
        "evict_workload_high_allowed",
        {
            "action": "evict_workload",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(kind="Namespace", name="app=checkout"),
        },
        True,
        None,
    ),
    # 21 — Phase 15: revert_drift at low → L3 → deny (operator approval).
    (
        "revert_drift_low_blocked",
        {
            "action": "revert_drift",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="nginx-demo"),
        },
        False,
        "L3",
    ),
    # 22 — Phase 15: revert_drift at high → L1 → allow.
    (
        "revert_drift_high_allowed",
        {
            "action": "revert_drift",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(kind="Deployment", name="nginx-demo"),
        },
        True,
        None,
    ),
    # 23 — Phase 15: revert_drift at critical in protected ns → deny by namespace.
    (
        "revert_drift_kubesystem_blocked",
        {
            "action": "revert_drift",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(namespace="kube-system", kind="Deployment", name="coredns"),
        },
        False,
        "protected",
    ),
    # 24 — Multiple gate violations stack reasons (kube-public + observe-only + L3).
    (
        "multiple_reasons_aggregated",
        {
            "action": "drain_node",
            "severity": "medium",
            "agent_mode": "observe-only",
            "target": _target(namespace="kube-public", kind="Node", name="x"),
        },
        False,
        None,  # we assert on count below
    ),
    # ── Phase 21: security-domain matrix ───────────────────────────────────
    # 25 — isolate_pod at high in workloads + active → L2 → allow.
    (
        "isolate_pod_high_active_workloads",
        {"action": "isolate_pod", "severity": "high", "agent_mode": "active", "target": _target()},
        True,
        None,
    ),
    # 26 — isolate_pod at critical → L1 → allow.
    (
        "isolate_pod_critical_active_workloads",
        {
            "action": "isolate_pod",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(),
        },
        True,
        None,
    ),
    # 27 — Escalate-first: isolate_pod at medium → L3 → deny (page instead).
    (
        "isolate_pod_medium_blocked_escalate_first",
        {
            "action": "isolate_pod",
            "severity": "medium",
            "agent_mode": "active",
            "target": _target(),
        },
        False,
        "L3",
    ),
    # 28 — isolate_pod blocked in observe-only even at critical (mode gate).
    (
        "isolate_pod_blocked_in_observe_only",
        {
            "action": "isolate_pod",
            "severity": "critical",
            "agent_mode": "observe-only",
            "target": _target(),
        },
        False,
        "agent_mode",
    ),
    # 29 — isolate_pod blocked in kube-system even at critical+active (protected).
    (
        "isolate_pod_kubesystem_blocked",
        {
            "action": "isolate_pod",
            "severity": "critical",
            "agent_mode": "active",
            "target": _target(namespace="kube-system", name="coredns-x"),
        },
        False,
        "protected",
    ),
    # 30 — apply_network_policy at high → L2 → allow (data-exfil containment).
    (
        "apply_network_policy_high_active_workloads",
        {
            "action": "apply_network_policy",
            "severity": "high",
            "agent_mode": "active",
            "target": _target(),
        },
        True,
        None,
    ),
    # 31 — kill_pod is most conservative: high → L3 → deny.
    (
        "kill_pod_high_blocked",
        {"action": "kill_pod", "severity": "high", "agent_mode": "active", "target": _target()},
        False,
        "L3",
    ),
    # 32 — kill_pod at critical → L2 → allow (the only severity it's permitted).
    (
        "kill_pod_critical_active_allowed",
        {"action": "kill_pod", "severity": "critical", "agent_mode": "active", "target": _target()},
        True,
        None,
    ),
    # 33 — capture_forensics is read-only → L1 at every severity → allow even at low.
    (
        "capture_forensics_low_active_allowed",
        {
            "action": "capture_forensics",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(),
        },
        True,
        None,
    ),
    # 34 — page_security always allowed (escalation never gated), even at low.
    (
        "page_security_low_active_allowed",
        {
            "action": "page_security",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(),
        },
        True,
        None,
    ),
]


@pytest.mark.parametrize(
    ("case_id", "payload", "expect_allow", "reason_contains"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_policy_decision(
    case_id: str,
    payload: dict,
    expect_allow: bool,
    reason_contains: str | None,
) -> None:
    result = _evaluate(payload)
    assert result["allow"] is expect_allow, (
        f"[{case_id}] expected allow={expect_allow}, got "
        f"allow={result['allow']} reasons={result['reasons']}"
    )
    if expect_allow:
        assert result["reasons"] == [], f"[{case_id}] allow=true but reasons={result['reasons']}"
    elif reason_contains is not None:
        assert any(reason_contains in r for r in result["reasons"]), (
            f"[{case_id}] expected a reason containing {reason_contains!r}; "
            f"got reasons={result['reasons']}"
        )


def test_multiple_violations_aggregate_reasons() -> None:
    """A request that trips multiple gates should surface ALL violated reasons,
    not just the first one — operators need the full picture in the audit log."""
    payload = {
        "action": "rollout_undo",  # L3 at low severity (matrix violation)
        "severity": "low",
        "agent_mode": "observe-only",  # mode violation
        "target": _target(namespace="kube-system", kind="Deployment", name="x"),  # protected ns
    }
    result = _evaluate(payload)
    reasons = result["reasons"]
    assert result["allow"] is False
    # Three independent gates should fire: matrix, mode, protected-ns.
    joined = " | ".join(reasons)
    assert "L3" in joined, f"expected L3 matrix reason in {reasons}"
    assert "agent_mode" in joined, f"expected mode reason in {reasons}"
    assert "protected" in joined, f"expected protected-ns reason in {reasons}"
    assert len(reasons) >= 3, f"expected >=3 reasons, got {reasons}"


def test_response_shape_is_stable() -> None:
    """The agent depends on `allow` and `reasons` fields. Guard against schema drift."""
    result = _evaluate(
        {
            "action": "page_human",
            "severity": "low",
            "agent_mode": "active",
            "target": _target(),
        }
    )
    assert isinstance(result["allow"], bool)
    assert isinstance(result["reasons"], list)
    assert all(isinstance(r, str) for r in result["reasons"])
