"""Pure-Python statistics used by the baseline learner.

We deliberately do NOT pull numpy or statsmodels here. The math is
small (≤ a few thousand samples per metric_key) and the edge target
must run on Jetson-class hardware where every megabyte of dependency
tree matters. Stdlib `statistics` + plain loops handle our scale fine.

Three primitives:

* `ewma_mean_var` — exponentially-weighted mean + variance. Welford-
  style online update so we never re-walk the full sample series.
* `hour_of_day_medians` — 24-bucket median, recomputed from the full
  window. Cheap (sorted on 24 sub-lists of ≤ 60 each) and good
  enough for "this metric is N σ above its 7-day baseline at this
  hour" diagnostic snapshots.
* `safe_zscore` — `(x - μ) / σ` with a tunable floor on σ to keep the
  output bounded when a metric is genuinely constant.

`quantile` is a re-implementation matching `agent.calibration.store
._quantile` (same linear interpolation) — kept local so the
baselines package has no cross-imports from calibration.

The minimum sample counts that gate snapshot upgrades are exposed as
module-level constants so the learner, the snapshot reader, and the
tests all stamp them in one place.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

# How many samples we want before any baseline is reported at all.
# 50 ≈ 50 minutes at the default 60 s scrape interval — short enough
# that local-dev demos don't have to wait, long enough that one outlier
# sample doesn't dominate μ ± σ.
BASELINE_MIN_SAMPLES = 50

# How many samples we want before we start trusting the per-hour median
# (the seasonal residual). 7 days × 24 h × 4 samples/h (15 min) = 672.
# We use the same threshold for the 60 s scrape (= 7 days × ~30 = 5 000)
# because the bottleneck is hour coverage, not raw count.
BASELINE_SEASONAL_MIN_SAMPLES = 672

# Buckets for the seasonal residual. 24 covers the most common pattern
# (daily). Weekly seasonality (workday vs weekend) would need 168 and
# materially more storage — left for Phase 14 forecasting if it turns
# out to matter.
BASELINE_SEASONAL_PERIODS = 24

# Floor on σ inside `safe_zscore`. Prevents (current − μ) / 0 when a
# metric is constant. Tuned so that a "tiny ripple" on a constant series
# (e.g. memory_rss jittering by 1 KiB) doesn't produce huge z-scores.
_Z_MIN_SIGMA = 1e-6


@dataclass(frozen=True)
class EwmaStats:
    mean: float
    variance: float
    n: int

    @property
    def std(self) -> float:
        # Variance can drift slightly negative on near-constant streams
        # due to floating-point error. Clamp so callers can call .std
        # freely without ValueError.
        return max(self.variance, 0.0) ** 0.5


def ewma_mean_var(values: Iterable[float], alpha: float = 0.05) -> EwmaStats:
    """Exponentially-weighted mean + variance over an iterable of samples.

    alpha = 0.05 means each new sample contributes ~5 % to the running
    mean. At 60 s scrape interval this gives a ~20-minute effective
    averaging window — slow enough to ignore single-tick chaos events,
    fast enough to track a real load shift within an hour.

    Returns mean = 0.0, variance = 0.0, n = 0 on empty input so callers
    don't have to guard.

    Algorithm: West (1979) recurrence for EWMA mean and unweighted
    rolling variance against that mean. Simpler than full Welford and
    sufficient because we don't need exact incremental variance — we
    just want a stable estimate to feed `safe_zscore`.
    """
    n = 0
    mean = 0.0
    s2 = 0.0  # accumulator for (x - μ)^2
    for v in values:
        x = float(v)
        n += 1
        if n == 1:
            mean = x
            s2 = 0.0
            continue
        new_mean = (1 - alpha) * mean + alpha * x
        # Variance estimate against the *current* EWMA mean — biased,
        # but stable, and good enough for z-score thresholding.
        s2 = (1 - alpha) * s2 + alpha * (x - new_mean) * (x - mean)
        mean = new_mean
    variance = s2 if n > 1 else 0.0
    return EwmaStats(mean=mean, variance=variance, n=n)


def safe_zscore(current: float, mean: float, std: float) -> float:
    """Bounded (current − mean) / std.

    Returns 0.0 when σ is below `_Z_MIN_SIGMA` to avoid the false
    "infinity σ above baseline" you get from a flat series with a
    micro-perturbation. Positive z means above baseline; negative
    means below. The graph node and the LLM prompt clamp negative
    values to "at baseline" since "well below baseline" rarely
    matters for SRE incidents (a sudden DROP in load is interesting
    but isn't what triggers OOMs / CPU starvation).
    """
    if std < _Z_MIN_SIGMA:
        return 0.0
    return (float(current) - float(mean)) / float(std)


def hour_of_day_medians(
    samples: Iterable[tuple[float, float]],
    periods: int = BASELINE_SEASONAL_PERIODS,
) -> list[float | None]:
    """Median value per hour-of-day bucket.

    Input: iterable of `(unix_ts_seconds, value)`. Output: list of
    length `periods`, one entry per bucket. A bucket with no samples
    yields `None`. Order is bucket index 0..periods-1; bucket index
    is `int(ts // 3600) % periods`.

    Cheap by construction: O(N) bucketing + O(B·log B) median per
    bucket where B is the largest bucket size. Even at the seasonal
    minimum (672 samples) the per-bucket count averages ~28.
    """
    buckets: list[list[float]] = [[] for _ in range(periods)]
    for ts, value in samples:
        idx = int(ts // 3600) % periods
        buckets[idx].append(float(value))
    out: list[float | None] = []
    for bucket in buckets:
        if not bucket:
            out.append(None)
        else:
            out.append(float(statistics.median(bucket)))
    return out


def quantile(samples: Iterable[float], q: float) -> float:
    """Linear-interpolation quantile. 0.0 on empty input.

    Mirrors `agent.calibration.store._quantile` so the two packages
    stay consistent on how p25/p50/p90 are reported in the SPA. We
    keep a local copy to avoid an inter-package import.
    """
    xs = sorted(float(v) for v in samples)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac
