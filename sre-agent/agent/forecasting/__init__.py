"""Predictive forecasting for cluster metrics.

Phase 14 — the proactive half of the Observe → Predict → Heal → Learn
loop. Builds on the Phase 13 baseline store: every fitted
`MetricKey` is a candidate for a short-horizon forecast.

Three responsibilities:

1. **Linear-regression forecaster.** Pure-Python ordinary least
   squares over the most-recent N samples for each `MetricKey`,
   yielding a slope + intercept and three forecast horizons
   (5 min, 15 min, 60 min). No numpy, no statsmodels — the math
   on ~200 samples runs in microseconds.

2. **Background scheduler.** Asyncio task started by `agent.serve`.
   On a fixed tick (default 60 s), walks every fitted baseline,
   computes the forecast, and publishes an
   `infra.events.forecasted.<kind>` event to NATS when the
   forecasted value at the 5-min horizon would cross a danger
   threshold (5 σ above the EWMA mean by default).

3. **Severity floor.** Forecast-driven events fire one severity
   step BELOW the equivalent reactive event so an operator drowning
   in real-time alerts doesn't get flooded with predicted ones too.

The store is the same SQLite DB used by Phase 13 baselines — a new
`forecasts` table sits alongside the existing `samples` + `baselines`
tables. Reads are lock-free; writes serialise through the same
`BaselineStore._lock`.
"""

from agent.forecasting.forecaster import (
    DEFAULT_HORIZONS_S,
    DangerLevel,
    Forecast,
    ForecastDecision,
    decide,
    fit_linear,
    forecast_at,
)
from agent.forecasting.scheduler import (
    DEDUP_WINDOW_S,
    DEFAULT_TICK_INTERVAL_S,
    SEVERITY_STEP_DOWN,
    ForecastScheduler,
    NATSPublisher,
    scheduler_from_env,
)

__all__ = [
    "DEDUP_WINDOW_S",
    "DEFAULT_HORIZONS_S",
    "DEFAULT_TICK_INTERVAL_S",
    "SEVERITY_STEP_DOWN",
    "DangerLevel",
    "Forecast",
    "ForecastDecision",
    "ForecastScheduler",
    "NATSPublisher",
    "decide",
    "fit_linear",
    "forecast_at",
    "scheduler_from_env",
]
