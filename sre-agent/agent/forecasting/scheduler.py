"""Background scheduler that runs the forecaster against every fitted
baseline and publishes forecasted incident events to NATS.

Started by `agent.serve` as an asyncio task. Tick cadence is set by
`FORECAST_TICK_INTERVAL_S` (default 60 s — matches the baseline
scrape cadence so each tick has fresh data).

On each tick the scheduler:

  1. Lists every `MetricKey` the baseline store knows about.
  2. Pulls the recent samples + fitted baseline for each key.
  3. Runs `fit_linear` over those samples.
  4. Calls `decide(...)` to classify the forecast against the
     baseline's EWMA mean + std.
  5. For `warning` / `critical` decisions, publishes an
     `infra.events.forecasted.<reason>` event to NATS. The agent's
     existing graph consumer picks it up like any other event.

Two important design choices:

* **Severity floor.** The published event's `severity` is ONE STEP
  DOWN from the reactive equivalent (high → medium, critical →
  high, etc.). False forecasts then can't bypass the OPA matrix at
  the reactive severity. Tunable per-(metric, namespace, pod) via
  the suppression list (see `_is_suppressed`).

* **Deduplication.** Each (metric_key, danger_level) tuple is
  suppressed for `DEDUP_WINDOW_S` after a publish, so a slow ramp
  doesn't generate a fresh event every tick. The cooldown is held
  in memory only — restart-on-crash will re-publish, which is the
  right safety default for a paused/restarted agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from agent.baselines.store import BaselineStore, MetricKey, get_default_store
from agent.config import settings
from agent.forecasting.forecaster import (
    DangerLevel,
    Forecast,
    ForecastDecision,
    decide,
    fit_linear,
)

log = logging.getLogger(__name__)


# Tick cadence. Same default as the baseline scrape interval so each
# tick operates on fresh data; clamp to ≥ 30 s so we can't accidentally
# saturate the cluster with forecast events.
DEFAULT_TICK_INTERVAL_S = 60

# How long to suppress a repeated (metric_key, level) emission. Five
# minutes balances "alert again if the situation persists" against
# the "event storm" failure mode where every tick re-fires.
DEDUP_WINDOW_S = 300

# Default reactive→forecasted severity step-down. Forecast-driven
# events fire at the lower severity so OPA's matrix is one step more
# strict than for an equivalent reactive event. critical is the only
# level that doesn't step down further — at that point the predicted
# breach is severe enough that the operator should see it at full
# severity.
SEVERITY_STEP_DOWN = {
    "critical": "high",
    "warning": "medium",
}

# How many samples to read per key for the linear fit. Smaller than
# the Phase 13 baseline window (1500) because a short tail captures
# the recent TREND, which is what forecasting cares about.
DEFAULT_FIT_WINDOW = 60


# ---------------------------------------------------------------------------
# NATS publisher abstraction. The real implementation publishes to
# JetStream; tests inject a fake.
# ---------------------------------------------------------------------------


class NATSPublisher:
    """Thin wrapper around `nats.aio.Client.publish`. Connect-on-demand;
    a missing NATS doesn't break the scheduler — events are dropped
    with a single WARNING. Mirrors the chaos_observer's failure mode."""

    def __init__(self, nats_url: str) -> None:
        self.nats_url = nats_url
        self._client: Any | None = None
        self._js: Any | None = None

    async def _ensure(self) -> bool:
        if self._js is not None:
            return True
        try:
            import nats  # type: ignore[import-not-found]
        except ImportError:
            log.warning("nats-py not installed; forecast events will be dropped")
            return False
        try:
            self._client = await nats.connect(self.nats_url)
            self._js = self._client.jetstream()
            try:
                await self._js.add_stream(name="INFRA", subjects=["infra.events.>"])
            except Exception:  # noqa: BLE001 — stream may already exist
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("NATS unreachable for forecasting (%s); dropping event", exc)
            self._client = None
            self._js = None
            return False

    async def publish(self, subject: str, payload: dict) -> bool:
        if not await self._ensure():
            return False
        try:
            await self._js.publish(subject, json.dumps(payload).encode("utf-8"))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("forecast publish failed (%s); dropped %s", exc, subject)
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.drain()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
            self._js = None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class ForecastScheduler:
    """Polling + publish loop. `tick_once()` is the unit of work tests
    drive directly; `start()` / `stop()` run it on a cadence."""

    def __init__(
        self,
        *,
        store: BaselineStore | None = None,
        publisher: NATSPublisher | None = None,
        tick_interval_s: int = DEFAULT_TICK_INTERVAL_S,
        fit_window: int = DEFAULT_FIT_WINDOW,
        dedup_window_s: int = DEDUP_WINDOW_S,
    ) -> None:
        self.store = store or get_default_store()
        self.publisher = publisher or NATSPublisher(settings.nats_url)
        self.tick_interval_s = max(30, int(tick_interval_s))
        self.fit_window = max(10, int(fit_window))
        self.dedup_window_s = max(60, int(dedup_window_s))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # In-memory dedup map: (metric_key_tuple, level) → unix_ts of
        # last publish. Cleared on process restart.
        self._cooldowns: dict[tuple[tuple[str, str, str], DangerLevel], float] = {}

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="forecast-scheduler")
        log.info(
            "forecasting: scheduler started (tick=%ds fit_window=%d dedup=%ds)",
            self.tick_interval_s,
            self.fit_window,
            self.dedup_window_s,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None
        await self.publisher.close()

    async def _run(self) -> None:
        # First tick is immediate so the operator sees activity right
        # after `make up`, not after the first 60-s wait.
        await self.tick_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_interval_s)
            except TimeoutError:
                pass
            if self._stop.is_set():
                return
            await self.tick_once()

    async def tick_once(self) -> int:
        """Walk every known baseline, forecast, and publish events for
        the warning / critical decisions. Returns the number of events
        actually published this tick."""
        published = 0
        try:
            keys = self.store.list_keys()
        except Exception as exc:  # noqa: BLE001
            log.warning("forecasting: store.list_keys failed (%s)", exc)
            return 0
        now = time.time()
        for key in keys:
            try:
                event = self._maybe_event(key, now)
            except Exception as exc:  # noqa: BLE001 — per-key soft fail
                log.warning("forecasting: %s raised (%s); skipping", key, exc)
                continue
            if event is None:
                continue
            ok = await self.publisher.publish(event["subject"], event["payload"])
            if ok:
                published += 1
                self._cooldowns[(key.as_tuple(), event["level"])] = now
                # Best-effort Prometheus emit so a dashboard can chart
                # forecast load + accuracy. Wrapped so a metrics import
                # failure can't break the publish path.
                try:
                    from agent.metrics import (
                        forecast_events_total,
                        forecast_r2,
                        forecast_sigma_above,
                    )

                    sigma = event["payload"]["output_fields"]["sigma_above"]
                    r2 = event["payload"]["output_fields"]["r2"]
                    forecast_events_total.labels(metric=key.metric, level=event["level"]).inc()
                    forecast_sigma_above.observe(float(sigma))
                    forecast_r2.observe(float(r2))
                except Exception as exc:  # noqa: BLE001
                    log.debug("forecast metric emit failed (%s); continuing", exc)
        if published:
            log.info("forecasting: published %d forecast event(s)", published)
        return published

    # --- internals --------------------------------------------------------

    def _maybe_event(self, key: MetricKey, now: float) -> dict | None:
        """Returns an event spec to publish, or None when:
        * the key has no baseline fit (cold-start),
        * the fit fails (too few samples / r² too low),
        * the decision is `safe` or `watch`, or
        * the key is in its dedup cooldown window.
        """
        fit_row = self.store.get_fit(key)
        if fit_row is None or fit_row.std < 1e-6:
            return None
        samples = self.store.recent_samples(key, limit=self.fit_window)
        forecast: Forecast | None = fit_linear([(s.ts, s.value) for s in samples])
        if forecast is None:
            return None
        decision: ForecastDecision = decide(forecast, fit_row.mean, fit_row.std)
        if decision.level in ("safe", "watch"):
            return None
        if self._is_suppressed(key, decision.level, now):
            return None
        subject = self._subject_for(key, decision.level)
        payload = self._payload(key, fit_row, forecast, decision)
        return {"subject": subject, "level": decision.level, "payload": payload}

    def _is_suppressed(self, key: MetricKey, level: DangerLevel, now: float) -> bool:
        last = self._cooldowns.get((key.as_tuple(), level))
        return last is not None and (now - last) < self.dedup_window_s

    def _subject_for(self, key: MetricKey, level: DangerLevel) -> str:
        """`infra.events.forecasted.<metric>.<level>`. The `infra.events.`
        prefix routes this event to the agent's existing JetStream
        consumer just like any reactive event."""
        return f"infra.events.forecasted.{key.metric}.{level}"

    def _payload(
        self,
        key: MetricKey,
        fit_row: Any,
        forecast: Forecast,
        decision: ForecastDecision,
    ) -> dict:
        """Build the Falco-shaped event payload. Mirrors the chaos
        observer's existing shape so the triage node doesn't need a
        new branch — `rule` carries the human-readable predictor name."""
        # Severity step-down (critical → high; warning → medium). The
        # default mapping is in SEVERITY_STEP_DOWN; unknown levels
        # fall back to "medium" rather than re-fire the original
        # reactive severity.
        severity_floor = SEVERITY_STEP_DOWN.get(decision.level, "medium")
        return {
            "rule": f"Forecasted {key.metric} breach ({decision.level})",
            "priority": severity_floor,
            "output": decision.reason,
            "output_fields": {
                "k8s.ns.name": key.namespace,
                "k8s.pod.name": key.scope,
                "metric": key.metric,
                "current": fit_row.mean,
                "forecast_t5_min": forecast.t5_min,
                "forecast_t15_min": forecast.t15_min,
                "forecast_t60_min": forecast.t60_min,
                "sigma_above": decision.sigma_above,
                "slope_per_s": forecast.slope_per_s,
                "r2": forecast.r2,
            },
            "source": "keos-forecaster",
            "ts": time.time(),
        }


# ---------------------------------------------------------------------------
# Entrypoint helper for `agent.serve`.
# ---------------------------------------------------------------------------


def scheduler_from_env() -> ForecastScheduler:
    """Build a scheduler with cadence + thresholds pulled from env.
    Each knob is independently overridable so an operator can damp
    the forecaster without a rebuild."""
    return ForecastScheduler(
        tick_interval_s=int(os.environ.get("FORECAST_TICK_INTERVAL_S", "60")),
        fit_window=int(os.environ.get("FORECAST_FIT_WINDOW", "60")),
        dedup_window_s=int(os.environ.get("FORECAST_DEDUP_WINDOW_S", "300")),
    )
