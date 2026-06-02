"""Read API used by the graph's `enrich_with_baselines` node.

Turns a fitted `BaselineFit` plus a fresh "current value" into a
short, LLM-readable snapshot:

    BaselineSnapshot(
        metric          = "container_memory_rss_bytes",
        namespace       = "workloads",
        scope           = "nginx-demo-7b8d…",
        status          = "ok" | "learning" | "missing",
        current         = 838 MiB,
        mean            = 412 MiB,
        std             = 18 MiB,
        sigma_above     = 23.6,
        seasonal_residual = 426 MiB,   # current − hour-of-day median
        n_samples       = 942,
    )

Status semantics:

* `"missing"`  — Prometheus has never reported a value for this key
                  (or the learner isn't running). The snapshot has
                  no current value; the LLM falls back to raw values.
* `"learning"` — the store has < `BASELINE_MIN_SAMPLES` samples; the
                  snapshot reports the current value but no baseline.
* `"ok"`       — full baseline available. The diagnose prompt weights
                  σ-above-baseline more than absolute values.

The reader also handles "current value" lookup: callers can either
pass in a known current value (the learner does this on its tick) or
ask the store to compute it from the most recent sample for the key.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Literal

from agent.baselines.math import (
    BASELINE_SEASONAL_PERIODS,
    safe_zscore,
)
from agent.baselines.store import (
    BaselineFit,
    BaselineStore,
    MetricKey,
    get_default_store,
)

log = logging.getLogger(__name__)


# How many distinct (metric, scope) snapshots the graph node will
# inject into the diagnose prompt at most. Bounded to keep the prompt
# focused — even a memory-leak incident usually has 3-4 relevant
# series, not dozens.
SNAPSHOT_TOP_N = 5

Status = Literal["ok", "learning", "missing"]


@dataclass
class BaselineSnapshot:
    metric: str
    namespace: str
    scope: str
    status: Status
    current: float | None = None
    mean: float | None = None
    std: float | None = None
    sigma_above: float | None = None
    seasonal_residual: float | None = None
    n_samples: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def is_anomalous(self) -> bool:
        """Heuristic used to rank snapshots before the top-N cut.

        A series is considered "anomalous" if σ-above-baseline ≥ 2 OR
        the seasonal residual is at least 1 σ. The graph node uses
        this so the LLM sees the *interesting* metrics first when we
        have to truncate to `SNAPSHOT_TOP_N`.
        """
        if self.status != "ok":
            return False
        if self.sigma_above is not None and self.sigma_above >= 2.0:
            return True
        if (
            self.seasonal_residual is not None
            and self.std is not None
            and self.std > 0
            and abs(self.seasonal_residual) / self.std >= 1.0
        ):
            return True
        return False


def snapshot_one(
    key: MetricKey,
    current: float | None,
    fit: BaselineFit | None,
    *,
    now: float | None = None,
) -> BaselineSnapshot:
    """Pure function: build a snapshot from a key + current + fit.

    Kept pure so unit tests can drive every status without touching
    the store. The graph node calls `snapshot_for_pod` which wires
    this up against the default store.
    """
    if current is None and fit is None:
        return BaselineSnapshot(
            metric=key.metric,
            namespace=key.namespace,
            scope=key.scope,
            status="missing",
        )
    if fit is None:
        # We have a current value but no fitted baseline yet — likely
        # the metric just appeared. The LLM gets the current value
        # raw; the prompt notes "baseline still learning".
        return BaselineSnapshot(
            metric=key.metric,
            namespace=key.namespace,
            scope=key.scope,
            status="learning",
            current=current,
        )
    sigma_above: float | None = None
    seasonal_residual: float | None = None
    if current is not None:
        sigma_above = safe_zscore(current, fit.mean, fit.std)
        bucket_idx = int((now or time.time()) // 3600) % BASELINE_SEASONAL_PERIODS
        median = fit.hour_medians[bucket_idx] if fit.hour_medians else None
        if median is not None:
            seasonal_residual = current - float(median)
    return BaselineSnapshot(
        metric=key.metric,
        namespace=key.namespace,
        scope=key.scope,
        status="ok",
        current=current,
        mean=fit.mean,
        std=fit.std,
        sigma_above=sigma_above,
        seasonal_residual=seasonal_residual,
        n_samples=fit.n_samples,
    )


def snapshot_for_pod(
    namespace: str,
    pod: str,
    *,
    store: BaselineStore | None = None,
    top_n: int = SNAPSHOT_TOP_N,
    now: float | None = None,
) -> list[BaselineSnapshot]:
    """Build the per-pod snapshot list the diagnose node injects.

    Pulls every known `(metric, namespace, scope=pod)` key, computes a
    snapshot per key, then returns at most `top_n` ranked by
    "anomalous first, then by descending |sigma_above|".

    Returns an empty list when the store has no data for that pod. The
    graph node treats that as "no baselines available" — same
    fail-OPEN-but-empty pattern as `k8sgpt_analyze`.
    """
    store = store or get_default_store()
    keys = store.list_keys(namespace=namespace, scope=pod)
    if not keys:
        return []
    snaps: list[BaselineSnapshot] = []
    for key in keys:
        # Most-recent sample defines "current". We pull just one row
        # via the recent_samples helper to avoid a separate query path.
        recent = store.recent_samples(key, limit=1)
        current = recent[-1].value if recent else None
        fit = store.get_fit(key)
        snaps.append(snapshot_one(key, current, fit, now=now))
    snaps.sort(
        key=lambda s: (
            0 if s.is_anomalous() else 1,
            -abs(s.sigma_above or 0.0),
            s.metric,
        )
    )
    return snaps[:top_n]
