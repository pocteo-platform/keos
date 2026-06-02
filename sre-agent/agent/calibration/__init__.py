"""Per-rule confidence calibration.

Phase 12 — the measurement foundation for the 90 %-confidence target.

This package owns three responsibilities:

1. **Telemetry.** Every diagnose step records `(rule_id, confidence)`;
   every terminal incident records `(rule_id, was_correct)`. Exposed
   via Prometheus histograms + the `/api/calibration` endpoint.

2. **Per-rule thresholds.** Instead of a single global
   `AGENT_CONFIDENCE_THRESHOLD = 0.6`, each `rule_id` gets its own.
   Rules with historically high accuracy keep a permissive threshold;
   rules that are routinely wrong escalate sooner. The graph's
   `_route_after_diagnose` consults this store before falling back to
   the global value.

3. **Operator override.** Operators can pin a threshold for a specific
   rule via the SPA (`POST /api/calibration/<rule>/threshold`). Override
   wins over the learned threshold; it persists across agent restarts.

The store is local SQLite (stdlib, no new deps). One row per rule_id.
Tracks rolling stats over the last N incidents (N configurable).
"""

from agent.calibration.grader import grade_outcome, record_diagnose
from agent.calibration.store import (
    CalibrationStore,
    RuleStats,
    get_default_store,
)

__all__ = [
    "CalibrationStore",
    "RuleStats",
    "get_default_store",
    "grade_outcome",
    "record_diagnose",
]
