"""Linear-regression forecaster — pure-Python OLS over Phase 13 samples.

Two layers:

* `fit_linear(samples)` returns `(slope, intercept, r2)` from ordinary
  least squares over `(ts, value)` pairs. Numerically stable on the
  small windows we use (≤ 1500 samples). Returns `None` when the
  input is too small or perfectly degenerate (all timestamps equal).

* `forecast_at(slope, intercept, when_ts)` evaluates the line at a
  future timestamp.

* `decide(snapshot, mean, std)` classifies a forecast into a
  `DangerLevel` by comparing the 5-min-ahead projection against the
  EWMA mean + std from the baseline store.

The decision matrix is intentionally conservative — false positives
in the forecasting layer become operator-facing alerts that drown
real ones. Tuning happens in two places: the `Z_TRIGGER_FORECAST`
threshold here, and the per-rule severity step-down in the
scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Forecast horizons in seconds. 5 min is the alert horizon; the 15-min
# and 60-min projections are passed to the diagnose LLM as additional
# context so it can weight "trending fast" vs "trending slow".
DEFAULT_HORIZONS_S: tuple[int, ...] = (300, 900, 3600)

# How many σ above the EWMA mean the 5-min forecast must reach before
# the scheduler emits an event. Tuned for: avoid alert fatigue on
# spiky-but-stationary metrics, catch a real load ramp before HPA's
# reactive threshold trips. Tunable via env (see scheduler.py).
Z_TRIGGER_FORECAST = 5.0

# R² floor — below this the linear fit is too noisy to act on. Avoids
# emitting events from random walk over the last 5 samples.
R2_MIN = 0.35

# Smallest sample count we'll fit. Lower than the Phase 13 baseline
# minimum because forecasting cares only about the recent trend, not
# the long-term mean.
N_MIN_FIT = 10

DangerLevel = Literal["safe", "watch", "warning", "critical"]


@dataclass(frozen=True)
class Forecast:
    """Output of a single forecast — values at each configured horizon."""

    slope_per_s: float  # value-units per second
    intercept: float  # value at t=0 (epoch seconds)
    r2: float  # 0..1, coefficient of determination
    n_samples: int
    horizons: dict[int, float]  # {horizon_seconds → predicted_value}

    @property
    def t5_min(self) -> float | None:
        return self.horizons.get(300)

    @property
    def t15_min(self) -> float | None:
        return self.horizons.get(900)

    @property
    def t60_min(self) -> float | None:
        return self.horizons.get(3600)


@dataclass(frozen=True)
class ForecastDecision:
    """Classification of a forecast against the baseline.

    `level` is the severity bucket the scheduler turns into a
    NATS event severity (one step DOWN from the equivalent reactive
    event — see scheduler.py for the mapping table).
    """

    level: DangerLevel
    sigma_above: float  # forecasted t5 in σ above mean
    reason: str  # human-readable, copied into the event payload


def fit_linear(samples: list[tuple[float, float]]) -> Forecast | None:
    """Ordinary least squares over `(timestamp, value)` pairs.

    Returns None when:
      * fewer than `N_MIN_FIT` samples,
      * timestamps span zero (denominator zero), or
      * r² is below `R2_MIN`.

    The R² floor catches "no real trend, just noise" cases that
    would otherwise spawn spurious forecast events.

    Numerical-stability note: timestamps are unix seconds (~ 1.7e9 in
    2026), so `t²` is around `3e18` — large enough that the naive
    `Σt² − n·mean_t²` form loses all significant digits to subtractive
    cancellation in float64, and the slope comes out as garbage close
    to zero. We center the timestamps (subtract `mean_t`) and work
    with the deviations, which keeps the magnitudes small and the
    arithmetic exact. The intercept is then computed back in the
    original frame so callers can evaluate `slope·t + intercept` at
    any unix timestamp.
    """
    n = len(samples)
    if n < N_MIN_FIT:
        return None
    # Centred-OLS pass 1: means.
    sum_t = 0.0
    sum_v = 0.0
    for t, v in samples:
        sum_t += t
        sum_v += v
    mean_t = sum_t / n
    mean_v = sum_v / n
    # Pass 2: deviations + product moments + variances.
    slope_num = 0.0
    slope_den = 0.0
    sstot = 0.0
    for t, v in samples:
        dt = t - mean_t
        dv = v - mean_v
        slope_num += dt * dv
        slope_den += dt * dt
        sstot += dv * dv
    if slope_den <= 0:
        # All timestamps identical — denominator is zero.
        return None
    if sstot <= 0:
        # Constant series → no variance to explain. Treat as
        # degenerate; the forecaster has nothing useful to say.
        return None
    slope = slope_num / slope_den
    intercept = mean_v - slope * mean_t
    # Pass 3: residual sum of squares for R².
    ssres = 0.0
    for t, v in samples:
        pred = slope * t + intercept
        ssres += (v - pred) ** 2
    r2 = max(0.0, min(1.0, 1.0 - ssres / sstot))
    if r2 < R2_MIN:
        return None
    horizons: dict[int, float] = {}
    # The "now" reference for the forecast is the latest sample's ts.
    # Forecasts at horizon h are evaluated at now + h.
    now = max(t for t, _ in samples)
    for h in DEFAULT_HORIZONS_S:
        horizons[h] = slope * (now + h) + intercept
    return Forecast(
        slope_per_s=slope,
        intercept=intercept,
        r2=r2,
        n_samples=n,
        horizons=horizons,
    )


def forecast_at(forecast: Forecast, when_ts: float) -> float:
    """Evaluate the fitted line at an arbitrary timestamp. Useful for
    callers that need a non-default horizon (e.g. the SPA showing a
    sparkline 10 min into the future)."""
    return forecast.slope_per_s * when_ts + forecast.intercept


def decide(
    forecast: Forecast,
    mean: float,
    std: float,
    *,
    sigma_trigger: float = Z_TRIGGER_FORECAST,
) -> ForecastDecision:
    """Classify a forecast against the baseline.

    Decision matrix (5-min horizon σ above mean):

      σ ≥ 2 × trigger   → critical
      σ ≥ trigger       → warning
      σ ≥ trigger / 2   → watch (logged, no event)
      otherwise          → safe

    σ is computed against the EWMA mean + std the Phase 13 baseline
    store keeps. When `std` is below a small floor the σ collapses
    to "compare to mean" which is a poor signal — we treat that case
    as `safe` rather than emit a noisy event.
    """
    if std < 1e-6 or forecast.t5_min is None:
        return ForecastDecision(
            level="safe",
            sigma_above=0.0,
            reason="forecast: insufficient signal (constant baseline)",
        )
    z = (forecast.t5_min - mean) / std
    if z >= 2 * sigma_trigger:
        return ForecastDecision(
            level="critical",
            sigma_above=z,
            reason=(
                f"forecast: t+5min would be {z:+.1f}σ above the EWMA mean "
                f"(≥ {2 * sigma_trigger:.0f}σ, r²={forecast.r2:.2f})"
            ),
        )
    if z >= sigma_trigger:
        return ForecastDecision(
            level="warning",
            sigma_above=z,
            reason=(
                f"forecast: t+5min would be {z:+.1f}σ above the EWMA mean "
                f"(≥ {sigma_trigger:.0f}σ, r²={forecast.r2:.2f})"
            ),
        )
    if z >= sigma_trigger / 2:
        return ForecastDecision(
            level="watch",
            sigma_above=z,
            reason=(
                f"forecast: t+5min would be {z:+.1f}σ above the EWMA mean "
                f"(≥ {sigma_trigger / 2:.1f}σ, r²={forecast.r2:.2f})"
            ),
        )
    return ForecastDecision(
        level="safe",
        sigma_above=z,
        reason=f"forecast: t+5min is {z:+.1f}σ above the EWMA mean — within tolerance",
    )
