"""Hermetic tests for the Phase 12 calibration package.

No live OPA, no live Ollama, no live cluster. Just the SQLite store +
the grader's pure classification logic + the integration with the
graph's `_route_after_diagnose`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.calibration.grader import _classify, grade_outcome, record_diagnose
from agent.calibration.store import (
    SAMPLE_RESERVOIR_SIZE,
    CalibrationStore,
    RuleStats,
    _quantile,
)


# ─── Store: schema, CRUD, idempotency ────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> CalibrationStore:
    return CalibrationStore(tmp_path / "cal.db")


def test_get_returns_empty_shell_for_unknown_rule(store: CalibrationStore) -> None:
    s = store.get("never-seen-rule")
    assert isinstance(s, RuleStats)
    assert s.rule_id == "never-seen-rule"
    assert s.n_total == 0
    assert s.accuracy is None  # avoid 0/0 = 0.0 in the UI
    assert s.mean_confidence is None


def test_record_diagnose_creates_row(store: CalibrationStore) -> None:
    store.record_diagnose("memory-leak", 0.85)
    s = store.get("memory-leak")
    assert s.n_total == 0  # outcomes haven't been graded yet
    assert s.sum_confidence == pytest.approx(0.85)
    assert s.samples == [0.85]


def test_record_diagnose_appends_samples_capped_at_reservoir(
    store: CalibrationStore,
) -> None:
    # Push more than the reservoir size; ensure we cap.
    for i in range(SAMPLE_RESERVOIR_SIZE + 50):
        store.record_diagnose("rule-a", (i % 100) / 100.0)
    s = store.get("rule-a")
    assert len(s.samples) == SAMPLE_RESERVOIR_SIZE
    # Quantiles should be computed from the retained tail.
    assert 0.0 <= s.p50_confidence <= 1.0
    assert s.p50_confidence <= s.p90_confidence


def test_record_outcome_increments_correct_counter(store: CalibrationStore) -> None:
    for _ in range(7):
        store.record_outcome("crashloop", "healed")
    for _ in range(3):
        store.record_outcome("crashloop", "escalated_other")
    s = store.get("crashloop")
    assert s.n_total == 10
    assert s.n_correct == 7
    assert s.n_escalated_other == 3
    assert s.n_graded == 10
    assert s.accuracy == pytest.approx(0.7)


def test_n_graded_excludes_stale_and_override(store: CalibrationStore) -> None:
    store.record_outcome("oom", "healed")
    store.record_outcome("oom", "stale")  # not graded
    store.record_outcome("oom", "override")  # not graded
    s = store.get("oom")
    assert s.n_total == 3
    assert s.n_graded == 1
    assert s.accuracy == pytest.approx(1.0)


def test_unknown_outcome_is_ignored(store: CalibrationStore) -> None:
    store.record_outcome("rule", "bogus")
    assert store.get("rule").n_total == 0


def test_threshold_override_pins_and_clears(store: CalibrationStore) -> None:
    store.set_threshold_override("rule-x", 0.8)
    assert store.get("rule-x").threshold_override == pytest.approx(0.8)
    assert store.get("rule-x").effective_threshold == pytest.approx(0.8)
    store.set_threshold_override("rule-x", None)
    assert store.get("rule-x").threshold_override is None


def test_threshold_override_clamped_to_unit_range(store: CalibrationStore) -> None:
    store.set_threshold_override("rule-y", 1.5)
    assert store.get("rule-y").threshold_override == pytest.approx(1.0)
    store.set_threshold_override("rule-y", -0.5)
    assert store.get("rule-y").threshold_override == pytest.approx(0.0)


def test_list_all_returns_every_known_rule(store: CalibrationStore) -> None:
    store.record_diagnose("a", 0.7)
    store.record_diagnose("b", 0.8)
    store.record_outcome("c", "healed")
    ids = sorted(s.rule_id for s in store.list_all())
    assert ids == ["a", "b", "c"]


def test_reset_wipes_one_rule(store: CalibrationStore) -> None:
    store.record_diagnose("keep", 0.9)
    store.record_diagnose("drop", 0.5)
    store.reset("drop")
    ids = {s.rule_id for s in store.list_all()}
    assert ids == {"keep"}


# ─── Threshold math: learned vs override vs global ──────────────────────


def test_learned_threshold_uses_global_when_n_graded_small(
    store: CalibrationStore,
) -> None:
    # < 20 graded incidents → global default (0.6) wins.
    for _ in range(5):
        store.record_diagnose("new-rule", 0.95)
        store.record_outcome("new-rule", "healed")
    s = store.get("new-rule")
    assert s.learned_threshold == pytest.approx(0.6)  # global default


def test_learned_threshold_uses_p25_after_warmup(store: CalibrationStore) -> None:
    # ≥ 20 graded → p25 of samples used (clamped to [0.3, 0.9]).
    for i in range(25):
        store.record_diagnose("warm-rule", 0.7 + i * 0.01)
        store.record_outcome("warm-rule", "healed")
    s = store.get("warm-rule")
    # p25 of [0.70..0.94] ≈ 0.755, clamped fine.
    assert 0.3 <= s.learned_threshold <= 0.9
    assert s.learned_threshold < 0.9
    assert s.learned_threshold > 0.5


# ─── Grader classification (pure function) ──────────────────────────────


def test_classify_stale_resolved() -> None:
    assert _classify({"rule": "x", "stale_resolved": True}) == "stale"


def test_classify_human_override_via_action_result() -> None:
    assert (
        _classify({"rule": "x", "action_result": {"human_override": True}})
        == "override"
    )


def test_classify_human_override_via_top_level_flag() -> None:
    assert _classify({"rule": "x", "human_override": True}) == "override"


def test_classify_escalated_lowconf_from_reason() -> None:
    state = {
        "rule": "x",
        "escalated": True,
        "escalation_reason": "diagnosis confidence 0.42 below threshold",
    }
    assert _classify(state) == "escalated_lowconf"


def test_classify_escalated_other_for_non_confidence_reason() -> None:
    state = {
        "rule": "x",
        "escalated": True,
        "escalation_reason": "OPA matrix denied",
    }
    assert _classify(state) == "escalated_other"


def test_classify_healed_requires_action_success_and_verified() -> None:
    state = {
        "rule": "x",
        "action_result": {"success": True, "tool": "restart_pod"},
        "verification": {"healed": True},
    }
    assert _classify(state) == "healed"


def test_classify_page_human_is_not_healed() -> None:
    # page_human action succeeds but cluster unchanged; agent set
    # escalated=True via the verify node so classification is escalated_other.
    state = {
        "rule": "x",
        "escalated": True,
        "escalation_reason": "page_human action",
        "action_result": {"success": True, "tool": "page_human"},
        "verification": {"healed": None},
    }
    assert _classify(state) == "escalated_other"


def test_classify_action_failed_lands_in_escalated_other() -> None:
    state = {
        "rule": "x",
        "action_result": {"success": False, "tool": "restart_pod"},
        "verification": {"healed": False},
    }
    assert _classify(state) == "escalated_other"


def test_classify_returns_string_even_with_minimal_state() -> None:
    # Defensive: a state with only a rule shouldn't crash.
    bucket = _classify({"rule": "x"})
    assert bucket in {"healed", "escalated_other", "stale", "override", "escalated_lowconf"}


# ─── Grader integration with the store ───────────────────────────────────


def test_grade_outcome_increments_correct_bucket(store: CalibrationStore) -> None:
    final = {
        "rule": "memory-leak",
        "action_result": {"success": True, "tool": "restart_pod"},
        "verification": {"healed": True},
    }
    bucket = grade_outcome(final, store=store)
    assert bucket == "healed"
    s = store.get("memory-leak")
    assert s.n_correct == 1
    assert s.n_total == 1


def test_grade_outcome_skips_when_no_rule(store: CalibrationStore) -> None:
    bucket = grade_outcome({}, store=store)
    assert bucket is None
    assert store.list_all() == []


def test_record_diagnose_silent_on_missing_rule(store: CalibrationStore) -> None:
    record_diagnose(None, 0.8, store=store)
    record_diagnose("rule", None, store=store)
    assert store.list_all() == []


# ─── Quantile helper ────────────────────────────────────────────────────


def test_quantile_empty_returns_zero() -> None:
    assert _quantile([], 0.5) == 0.0


def test_quantile_single_returns_value() -> None:
    assert _quantile([0.42], 0.99) == 0.42


def test_quantile_interpolates_correctly() -> None:
    # [0.0, 0.5, 1.0] — p50 should be 0.5 exactly.
    assert _quantile([0.0, 0.5, 1.0], 0.5) == pytest.approx(0.5)
    # p25 = 0.25 (midway between 0.0 and 0.5)
    assert _quantile([0.0, 0.5, 1.0], 0.25) == pytest.approx(0.25)
