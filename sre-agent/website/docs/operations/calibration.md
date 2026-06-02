---
id: calibration
slug: /operations/calibration
title: Confidence calibration
sidebar_position: 5
description: How the Keos SRE Agent tracks its own diagnostic confidence, when it escalates, and how operators tune the per-rule threshold.
---

# Confidence calibration

The agent's `diagnose` step produces a **confidence score** alongside
its JSON diagnosis. The threshold the score has to clear determines
whether the agent acts or escalates. Phase 12 makes both sides of
that gate observable:

- the **confidence the LLM is actually producing** per rule, over time
- the **threshold each rule is gated on** right now — and where that
  threshold came from (operator override / learned from outcomes /
  global default).

The target is **≥ 90 % median confidence** across all rules the
cluster has seen. The Calibration view in the SPA shows you the gap.

## What gets measured

Two storage objects:

- **`keos_diagnose_confidence{rule}`** — a Prometheus histogram bumped
  once per incident at the diagnose step. Captures the distribution of
  reported confidence per rule.
- **`keos_diagnose_outcome_total{rule, bucket}`** — a counter bumped
  once at incident termination. Bucket is one of:

| bucket | meaning | counted as |
|---|---|---|
| `healed` | Agent acted; verify confirmed cluster recovered | **correct** |
| `escalated_lowconf` | Confidence didn't clear the threshold; agent refused to guess | neutral (the refusal is the safe behaviour) |
| `escalated_other` | Policy denied OR execute failed OR verify didn't heal | **incorrect** |
| `stale` | Triage short-circuited (pod already healthy) | excluded from accuracy |
| `override` | Operator approved an L3/L4 denial via the SPA | excluded (operator made the call) |

`accuracy = n_correct / n_graded` where `n_graded = n_total - n_stale - n_override`.

## How the threshold is chosen

`_route_after_diagnose` consults three sources in priority order:

1. **Operator override** (highest priority). Set via the SPA's
   Calibration view → `Pin` button → enter a value in `[0, 1]`.
   Persisted to `data/calibration.db`; survives restart. Cleared by
   the `Clear` button.
2. **Learned threshold** (middle). After ≥ 20 graded incidents, the
   25th percentile of that rule's observed confidence samples becomes
   the gate. Clamped to `[0.3, 0.9]` so we never go too lenient or too
   strict.
3. **Global default** (fallback). `AGENT_CONFIDENCE_THRESHOLD` env var
   (default `0.6`). Used for new rules with no history yet.

The agent surfaces which source won via the audit log:

```text
diagnosis confidence 0.42 < 0.70 (rule=memory-leak, source=learned) -> escalate
```

## Reading the Calibration view

The SPA's **Calibration** page (sidebar → Calibration) shows:

- Cluster-wide summary: median confidence, % of rules at ≥ 90 %, total
  rules tracked.
- Per-rule table: total / graded / accuracy / p50 / p90 / current
  threshold / pin-or-edit action.

Rules with low accuracy float to the top so operators see them first.

## Tuning a threshold

Common situations:

- **A rule is escalating too often despite seeming-correct diagnoses.**
  Look at its p50: if the LLM is reporting ~0.78 and the threshold is
  0.85, the rule is probably under-calibrated. Pin a permissive
  threshold like 0.7 and watch outcomes.

- **A rule keeps acting wrongly.** Pin a stricter threshold to force
  more escalations. Combine with a runbook edit (see the [SPA walkthrough](ui-walkthrough))
  to give the LLM better hints.

- **A new event kind just appeared and is being treated as global default 0.6.**
  Leave it. Wait for 20+ graded incidents; the learned threshold will
  settle.

The threshold is reversible — `Clear` removes the override and the
agent reverts to learned/global immediately.

## What's not in Phase 12 — and what is

| | |
|---|---|
| **In Phase 12** | confidence histograms, per-rule threshold store, operator override UI, accuracy bookkeeping |
| **Not yet** | the LLM doesn't learn from this data — it's measurement only. Outcome-driven rerun + RAG re-ranking is Phase 16. ML baselines feeding richer context to the LLM is Phase 13. |

Phase 12 is the measurement foundation; **Phases 13 / 14 / 16 are what
actually lift the median to ≥ 90 %**.

## Operator runbook for "median below 90 %"

1. Open `/calibration` in the SPA.
2. Sort by Accuracy (default) — rules below 75 % are at the top.
3. For each:
   - Read recent incidents on that rule from `/incidents?rule=<id>`.
   - Were they wrong because the runbook was misleading? Fix the
     runbook in `agent/runbooks/`; `make seed` re-embeds it. See the
     [use-case authoring guide](ui-walkthrough#authoring-a-use-case)
     for how to ship the fix through the SPA.
   - Were they wrong because the LLM didn't have enough signal? Wait
     for Phase 13 (baselines) — there's no operator dial today.
   - Were they "escalated_lowconf" but actually-correct diagnoses?
     Pin a more permissive threshold.
4. Re-check the median in 24 h.
