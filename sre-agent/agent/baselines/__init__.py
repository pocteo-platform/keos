"""ML baselines for cluster metrics.

Phase 13 — the biggest single confidence lever toward the 90 % target.
Instead of letting the LLM reason from raw event payloads alone, every
diagnose call gets a structured "this metric is N σ above its 7-day
baseline" snapshot per relevant cluster metric.

Three responsibilities:

1. **Sample collection.** A background learner polls Prometheus on a
   fixed cadence (default 60 s) and persists samples into a small
   SQLite database. Samples are pruned past `BASELINE_RETAIN_DAYS`.

2. **Baseline fitting.** Every `BASELINE_FIT_INTERVAL_S` seconds the
   learner recomputes per-(metric, scope) baselines from the most
   recent sample window — EWMA mean + rolling standard deviation,
   plus a per-hour-of-day median for daily seasonality.

3. **Snapshot lookup.** The graph's `enrich_with_baselines` node calls
   `snapshot_for_pod(ns, pod)` to get a short, LLM-readable list of
   baseline snapshots for the incident's target pod. Cold-start is
   handled gracefully — when there is not enough history yet, the
   snapshot's `status` field is `"learning"` and the diagnose prompt
   tells the LLM to fall back to raw values.

The store is local SQLite (stdlib, no new deps). All math is pure
Python — the edge footprint pays no numpy / statsmodels tax.
"""

from agent.baselines.math import (
    BASELINE_SEASONAL_PERIODS,
    ewma_mean_var,
    hour_of_day_medians,
    quantile,
    safe_zscore,
)
from agent.baselines.snapshot import (
    BaselineSnapshot,
    get_default_store,
    snapshot_for_pod,
    snapshot_one,
)
from agent.baselines.store import (
    BaselineFit,
    BaselineStore,
    MetricKey,
    Sample,
)

__all__ = [
    "BASELINE_SEASONAL_PERIODS",
    "BaselineFit",
    "BaselineSnapshot",
    "BaselineStore",
    "MetricKey",
    "Sample",
    "ewma_mean_var",
    "get_default_store",
    "hour_of_day_medians",
    "quantile",
    "safe_zscore",
    "snapshot_for_pod",
    "snapshot_one",
]
