"""Hermetic tests for the Phase 21 security domain.

Proves a `security.events.*`-shaped event flows through the SAME graph as
the SRE domain but is classified `domain=security` and routed to the
security tool catalog + prompts. Mocks every external service via the
shared conftest fixtures, so the whole file runs in well under a second.

Live telemetry (Falco webhook / Tetragon observer) is exercised separately
on real eBPF hardware; the OPA matrix rows are covered by test_policy.py.
"""

from __future__ import annotations

import pytest

from agent.graph import run_once
from agent.security_events import build_event


def _sec_event(scenario: str) -> dict:
    # The cluster pod-lookup subprocess is patched out by fake_kubectl
    # (returns rc=0 stdout=""), so build_event falls back to the conceptual
    # pod name "nginx-demo".
    return build_event(scenario)


def test_triage_classifies_security_domain(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """An event with source=falco/tetragon must be tagged domain=security
    in triage, regardless of mode."""
    patch_llm("security_reverse_shell")
    fake_mode_store.mode = "observe-only"

    final = run_once(_sec_event("reverse-shell"))
    assert final["domain"] == "security"


def test_security_active_mode_isolates_pod(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """In active mode, a high-confidence critical reverse-shell diagnosis
    plans isolate_pod, OPA allows it (critical → L1), and the security
    tool dispatch runs it (not the SRE catalog)."""
    patch_llm("security_reverse_shell")
    fake_mode_store.mode = "active"
    # OPA must actually allow — patch_opa default allows; the matrix itself
    # is covered in test_policy.py.
    patch_opa(allow=True, reasons=[])

    final = run_once(_sec_event("reverse-shell"))

    assert final["domain"] == "security"
    assert final["proposed_action"]["tool"] == "isolate_pod"
    # The action result must come from the security tool, which stamps
    # a reversible/network_policy meta block.
    result = final["action_result"]
    assert result["tool"] == "isolate_pod"
    assert result["dry_run"] is False
    # The deny-all NetworkPolicy name is derived from the pod name.
    assert "network_policy" in (result.get("meta") or {})


def test_security_observe_only_escalates_without_containment(
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """observe-only: the plan still chooses isolate_pod, but OPA's mode
    gate denies it, so the graph escalates WITHOUT mutating the cluster.
    The isolate_pod action must run in dry-run (no real kubectl apply)."""
    patch_llm("security_reverse_shell")
    fake_mode_store.mode = "observe-only"
    # Simulate OPA denying because of the mode gate.
    patch_opa(allow=False, reasons=['agent_mode is "observe-only" (must be "active")'])

    final = run_once(_sec_event("reverse-shell"))

    assert final["domain"] == "security"
    assert final["escalated"] is True
    # No live containment: the execute node, if it ran at all, did so dry-run.
    result = final.get("action_result") or {}
    if result:
        assert result["dry_run"] is True


@pytest.mark.parametrize("scenario", ["reverse-shell", "crypto-miner", "priv-esc", "data-exfil"])
def test_all_four_security_scenarios_build_and_route(
    scenario: str,
    patch_warmup,
    patch_llm,
    patch_opa,
    fake_qdrant,
    fake_kubectl,
    fake_mode_store,
    fake_k8sgpt,
    tmp_audit,
) -> None:
    """Every workshop scenario produces a security-domain incident that runs
    end-to-end (we reuse the reverse-shell canned LLM for plan/diagnose —
    this test pins the domain routing + event shape, not the LLM choice)."""
    patch_llm("security_reverse_shell")
    fake_mode_store.mode = "active"
    patch_opa(allow=True, reasons=[])

    event = _sec_event(scenario)
    assert event["domain"] == "security"
    assert event["source"] in ("falco", "tetragon")

    final = run_once(event)
    assert final["domain"] == "security"
    # The incident reached a terminal node (healed/verify or escalate).
    assert final.get("proposed_action", {}).get("tool") == "isolate_pod"
