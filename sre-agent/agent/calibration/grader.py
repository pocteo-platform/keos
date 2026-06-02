"""Outcome grader.

Maps a terminal IncidentState into one of five buckets that the
CalibrationStore counters care about. Plus a thin `record_diagnose`
helper for the diagnose node to record per-rule confidence samples.

Bucket semantics (matters for the accuracy ratio):

  - `healed`            — diagnosis was acted on, verify confirmed cluster
                          state recovered. The agent got it right.
  - `escalated_lowconf` — diagnose returned a confidence below the
                          per-rule threshold; routed to escalate without
                          acting. NOT counted as wrong — the agent
                          refused to guess, which is the safe behaviour.
  - `escalated_other`   — diagnose was confident enough but execute
                          failed / verify failed / policy denied. The
                          diagnosis was wrong OR the action was wrong;
                          either way, the agent missed.
  - `stale`             — triage short-circuited as stale-resolved (pod
                          already Ready by the time the agent got there).
                          Doesn't contribute to accuracy — the agent
                          never actually diagnosed.
  - `override`          — operator approved an action via the human-override
                          path. Doesn't contribute — the operator made the
                          call, not the agent.

The `n_graded` field on RuleStats excludes `stale` + `override` from the
denominator, so accuracy is honest about what the agent itself did.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.calibration.store import CalibrationStore, get_default_store

log = logging.getLogger(__name__)


def record_diagnose(
    rule_id: str | None,
    confidence: float | None,
    *,
    store: CalibrationStore | None = None,
) -> None:
    """Called by the diagnose node after the LLM returns. No-op if either
    field is missing (graph internal short-circuit). The metrics histogram
    is also bumped by the caller, NOT here, to keep this module storage-
    only."""
    if not rule_id or confidence is None:
        return
    store = store or get_default_store()
    try:
        store.record_diagnose(rule_id, float(confidence))
    except Exception as exc:  # noqa: BLE001 — calibration must never break the graph
        log.warning("calibration: record_diagnose failed (%s); ignoring", exc)


def grade_outcome(
    final_state: dict[str, Any],
    *,
    store: CalibrationStore | None = None,
) -> str | None:
    """Inspect a terminal IncidentState; classify into one of the five
    buckets; record in the store. Returns the bucket tag (for tests +
    audit) or None if there's no rule_id.

    Called exactly once per incident, from `serve.py` after the graph
    saves the final state.

    Behaviour:
      - Stale + override are checked FIRST because they short-circuit
        the agent's actual diagnose; we don't want to penalise the
        agent for an event it never reasoned over.
      - escalated_lowconf vs escalated_other is determined from the
        `escalation_reason`: phrases set by the graph's own
        `_route_after_diagnose` indicate low-confidence; everything else
        (policy_gate deny, execute failure, verify not-healed) is
        "other".
      - `healed` requires both action_result.success AND
        verification.healed (= True). The page_human case is treated
        as `escalated_other` because the cluster wasn't actually healed
        even though the action ran.
    """
    rule = final_state.get("rule")
    if not rule:
        return None
    store = store or get_default_store()

    bucket = _classify(final_state)
    try:
        store.record_outcome(rule, bucket)
    except Exception as exc:  # noqa: BLE001
        log.warning("calibration: record_outcome failed (%s); ignoring", exc)
    return bucket


def _classify(state: dict[str, Any]) -> str:
    """Pure function. Map terminal state to bucket. Keeps the test
    surface tiny."""
    if state.get("stale_resolved"):
        return "stale"

    # Operator approval through the human-override pathway. We set this
    # flag from agent/approvals.py::approve when an L3 denial is
    # operator-pushed-through.
    if _was_human_override(state):
        return "override"

    if state.get("escalated"):
        # Distinguish low-confidence escalation (agent refused) from
        # all other escalation reasons (agent failed at some later step).
        reason = (state.get("escalation_reason") or "").lower()
        if "confidence" in reason or "conf<" in reason:
            return "escalated_lowconf"
        return "escalated_other"

    ar = state.get("action_result") or {}
    ver = state.get("verification") or {}
    if ar.get("success") and ver.get("healed") is True:
        return "healed"

    # Action succeeded but cluster not actually healed (e.g., page_human),
    # or action failed silently without setting escalated. Either way the
    # agent didn't deliver a heal.
    return "escalated_other"


def _was_human_override(state: dict[str, Any]) -> bool:
    """`human_override=True` is set by agent/approvals.py when an
    operator approves an L3/L4 denied action via the SPA. We check the
    canonical location (action_result) plus the audit-friendly
    top-level field used by the SPA bulk-delete recipe."""
    ar = state.get("action_result") or {}
    if ar.get("human_override") is True:
        return True
    return bool(state.get("human_override"))
