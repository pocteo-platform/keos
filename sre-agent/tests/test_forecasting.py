"""Hermetic tests for the Phase 14 forecasting package.

No live NATS, no live Prometheus. We exercise:

  * Pure math (`fit_linear`, `decide`) over canned sample series.
  * The store integration via the existing `BaselineStore` fixture.
  * The scheduler's `tick_once` end-to-end with a fake publisher.
  * The severity step-down + dedup cooldown logic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from agent.baselines.store import BaselineFit, BaselineStore, MetricKey, Sample
from agent.forecasting.forecaster import (
    DEFAULT_HORIZONS_S,
    N_MIN_FIT,
    R2_MIN,
    Z_TRIGGER_FORECAST,
    decide,
    fit_linear,
    forecast_at,
)
from agent.forecasting.scheduler import (
    SEVERITY_STEP_DOWN,
    ForecastScheduler,
)

# ─── fit_linear ─────────────────────────────────────────────────────────


def test_fit_linear_returns_none_below_min_samples() -> None:
    samples = [(float(i), 1.0) for i in range(N_MIN_FIT - 1)]
    assert fit_linear(samples) is None


def test_fit_linear_returns_none_on_constant_series() -> None:
    """A perfectly flat series has zero variance to explain — R² is
    undefined (or 0/0) and there's no useful forecast."""
    samples = [(float(i), 42.0) for i in range(50)]
    assert fit_linear(samples) is None


def test_fit_linear_returns_none_on_identical_timestamps() -> None:
    """If every sample has the same ts, the OLS denominator is zero —
    we must NOT divide by it."""
    samples = [(100.0, float(i)) for i in range(20)]
    assert fit_linear(samples) is None


def test_fit_linear_recovers_known_slope_and_intercept() -> None:
    """Perfect line y = 2x + 10 should reconstruct exactly."""
    samples = [(float(t), 2.0 * t + 10.0) for t in range(20)]
    fit = fit_linear(samples)
    assert fit is not None
    assert fit.slope_per_s == pytest.approx(2.0, abs=1e-6)
    assert fit.intercept == pytest.approx(10.0, abs=1e-6)
    assert fit.r2 == pytest.approx(1.0, abs=1e-6)
    assert fit.n_samples == 20


def test_fit_linear_low_r2_rejected() -> None:
    """Pure noise (no real trend) → r² ~ 0 → discard the fit."""
    import random as _random

    _random.seed(0)
    samples = [(float(t), _random.random()) for t in range(50)]
    assert fit_linear(samples) is None  # R² well below R2_MIN


def test_fit_linear_populates_all_default_horizons() -> None:
    samples = [(float(t), 2.0 * t + 10.0) for t in range(20)]
    fit = fit_linear(samples)
    assert fit is not None
    for horizon in DEFAULT_HORIZONS_S:
        assert horizon in fit.horizons


def test_forecast_at_evaluates_line() -> None:
    samples = [(float(t), 2.0 * t + 10.0) for t in range(20)]
    fit = fit_linear(samples)
    assert fit is not None
    # value at t=100 → 2*100 + 10 = 210
    assert forecast_at(fit, 100.0) == pytest.approx(210.0, abs=1e-6)


def test_fit_linear_handles_unix_epoch_timestamps() -> None:
    """Regression guard: timestamps are unix seconds in production
    (~1.7e9 in 2026), so the naive OLS form `Σt² − n·mean_t²` loses
    all significant digits to subtractive cancellation in float64.
    The centered-OLS implementation must recover the true slope."""
    base = 1_700_000_000.0  # 2023 unix seconds
    samples = [(base + i, 100.0 + 2.0 * i) for i in range(50)]
    fit = fit_linear(samples)
    assert fit is not None
    # Slope MUST be ~2.0 per second, not collapsed to ~0 by precision loss.
    assert fit.slope_per_s == pytest.approx(2.0, abs=1e-6)
    # And the forecast at the next second should be the next sample value.
    next_value = fit.slope_per_s * (base + 50) + fit.intercept
    assert next_value == pytest.approx(200.0, abs=1e-3)


# ─── decide ─────────────────────────────────────────────────────────────


def _fit_climbing_at(target_t5: float) -> Any:
    """Helper: synthesize a Forecast whose t5_min equals `target_t5`."""

    class _F:
        slope_per_s = 0.0
        intercept = 0.0
        r2 = 0.99
        n_samples = 50
        horizons = {300: target_t5, 900: target_t5, 3600: target_t5}
        t5_min = target_t5
        t15_min = target_t5
        t60_min = target_t5

    return _F()


def test_decide_safe_when_below_half_trigger() -> None:
    fit = _fit_climbing_at(105.0)
    # mean=100, std=10 → z = 0.5σ → below trigger/2
    out = decide(fit, mean=100.0, std=10.0)
    assert out.level == "safe"


def test_decide_watch_when_between_half_and_trigger() -> None:
    # z = trigger/2 + ε
    z_target = Z_TRIGGER_FORECAST / 2 + 0.1
    fit = _fit_climbing_at(100.0 + z_target * 10.0)
    out = decide(fit, mean=100.0, std=10.0)
    assert out.level == "watch"


def test_decide_warning_when_above_trigger() -> None:
    fit = _fit_climbing_at(100.0 + (Z_TRIGGER_FORECAST + 0.1) * 10.0)
    out = decide(fit, mean=100.0, std=10.0)
    assert out.level == "warning"


def test_decide_critical_when_above_double_trigger() -> None:
    fit = _fit_climbing_at(100.0 + (2 * Z_TRIGGER_FORECAST + 0.1) * 10.0)
    out = decide(fit, mean=100.0, std=10.0)
    assert out.level == "critical"


def test_decide_safe_on_zero_std() -> None:
    """A constant baseline (std≈0) shouldn't surface forecasts —
    `safe_zscore`-style guard."""
    fit = _fit_climbing_at(9999.0)
    out = decide(fit, mean=100.0, std=0.0)
    assert out.level == "safe"
    assert "constant" in out.reason


def test_decide_carries_reason_string() -> None:
    fit = _fit_climbing_at(100.0 + (Z_TRIGGER_FORECAST + 0.1) * 10.0)
    out = decide(fit, mean=100.0, std=10.0)
    assert "σ" in out.reason
    assert "r²" in out.reason


# ─── ForecastScheduler.tick_once ────────────────────────────────────────


class FakePublisher:
    """In-memory replacement for `NATSPublisher` used in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: dict) -> bool:
        self.calls.append((subject, payload))
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
def store(tmp_path: Path) -> BaselineStore:
    return BaselineStore(tmp_path / "baselines.db")


def _seed_climbing_key(store: BaselineStore, key: MetricKey, n: int = 50) -> None:
    """Seeds a baseline + samples for a metric whose value climbs
    linearly at a slope steep enough to trip the forecaster."""
    now = time.time()
    # Baseline mean=100, std=5 → tripping the warning threshold means
    # forecasted t5 ≥ 100 + Z_TRIGGER_FORECAST * 5 = 100 + 25 = 125.
    # We make samples climb fast enough that t5 ≈ 200.
    store.insert_samples(
        [Sample(key=key, ts=now - (n - i), value=100.0 + i * 1.0) for i in range(n)]
    )
    store.upsert_fit(
        BaselineFit(
            key=key,
            mean=100.0,
            std=5.0,
            n_samples=n,
            hour_medians=[None] * 24,
            computed_at=now,
        )
    )


def _seed_stationary_key(store: BaselineStore, key: MetricKey, n: int = 50) -> None:
    """Stationary key — value oscillates around the mean but no trend.
    Should NOT trigger the forecaster."""
    now = time.time()
    store.insert_samples(
        [Sample(key=key, ts=now - (n - i), value=100.0 + (i % 3 - 1) * 0.5) for i in range(n)]
    )
    store.upsert_fit(
        BaselineFit(
            key=key,
            mean=100.0,
            std=2.0,
            n_samples=n,
            hour_medians=[None] * 24,
            computed_at=now,
        )
    )


@pytest.mark.asyncio
async def test_tick_publishes_event_on_climbing_metric(store: BaselineStore) -> None:
    key = MetricKey("container_memory_rss_bytes", "workloads", "leaker")
    _seed_climbing_key(store, key)
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30)
    published = await sched.tick_once()
    assert published == 1
    subject, payload = pub.calls[0]
    assert subject.startswith("infra.events.forecasted.")
    assert "container_memory_rss_bytes" in subject
    # Severity step-down: critical → high; warning → medium.
    assert payload["priority"] in {"medium", "high"}
    fields = payload["output_fields"]
    assert fields["metric"] == "container_memory_rss_bytes"
    assert fields["k8s.pod.name"] == "leaker"
    assert fields["k8s.ns.name"] == "workloads"
    assert fields["sigma_above"] > Z_TRIGGER_FORECAST
    assert payload["source"] == "keos-forecaster"


@pytest.mark.asyncio
async def test_tick_skips_stationary_metric(store: BaselineStore) -> None:
    key = MetricKey("container_memory_rss_bytes", "workloads", "calm")
    _seed_stationary_key(store, key)
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30)
    assert await sched.tick_once() == 0
    assert pub.calls == []


@pytest.mark.asyncio
async def test_tick_skips_key_without_baseline_fit(store: BaselineStore) -> None:
    """A key with samples but no fitted baseline shouldn't forecast."""
    key = MetricKey("container_memory_rss_bytes", "workloads", "no-baseline")
    store.insert_samples(
        [Sample(key=key, ts=time.time() - (50 - i), value=100.0 + i) for i in range(50)]
    )
    # Note: NO upsert_fit — baseline missing.
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30)
    assert await sched.tick_once() == 0


@pytest.mark.asyncio
async def test_tick_dedups_repeated_publishes(store: BaselineStore) -> None:
    key = MetricKey("container_memory_rss_bytes", "workloads", "leaker")
    _seed_climbing_key(store, key)
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30, dedup_window_s=300)
    n1 = await sched.tick_once()
    n2 = await sched.tick_once()
    assert n1 == 1
    assert n2 == 0  # within cooldown window
    # Manually expire the cooldown and retick — should publish again.
    for k in list(sched._cooldowns.keys()):
        sched._cooldowns[k] = 0.0  # force way-in-the-past
    n3 = await sched.tick_once()
    assert n3 == 1


@pytest.mark.asyncio
async def test_tick_severity_floor_steps_down(store: BaselineStore) -> None:
    """Critical forecasts should publish at 'high' (one step down);
    warning forecasts at 'medium'. The mapping comes from
    SEVERITY_STEP_DOWN."""
    key = MetricKey("container_memory_rss_bytes", "workloads", "leaker")
    _seed_climbing_key(store, key)
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30)
    await sched.tick_once()
    _, payload = pub.calls[0]
    # rule looks like "Forecasted ... (critical)" — pull the level.
    level = payload["rule"].split("(")[-1].rstrip(")")
    expected = SEVERITY_STEP_DOWN[level]
    assert payload["priority"] == expected, (
        f"level={level} expected priority={expected} got {payload['priority']}"
    )


@pytest.mark.asyncio
async def test_tick_swallows_per_key_errors(store: BaselineStore) -> None:
    """One bad key shouldn't poison the loop — other keys still get
    processed."""
    good = MetricKey("container_memory_rss_bytes", "workloads", "good")
    bad = MetricKey("container_memory_rss_bytes", "workloads", "bad")
    _seed_climbing_key(store, good)
    _seed_climbing_key(store, bad)
    pub = FakePublisher()
    sched = ForecastScheduler(store=store, publisher=pub, tick_interval_s=30)

    # Force `_maybe_event` to raise for the `bad` key.
    real = sched._maybe_event

    def _raising(key, now):
        if key == bad:
            raise RuntimeError("synthetic")
        return real(key, now)

    sched._maybe_event = _raising  # type: ignore[assignment]
    published = await sched.tick_once()
    # The good key still publishes; the bad one is swallowed.
    assert published == 1


# ─── Constants sanity ──────────────────────────────────────────────────


def test_severity_step_down_covers_all_emitted_levels() -> None:
    """Every danger level the scheduler can publish at MUST map to a
    floor — otherwise we'd silently default to 'medium' and miss
    documenting it."""
    assert "warning" in SEVERITY_STEP_DOWN
    assert "critical" in SEVERITY_STEP_DOWN
    # And the step-down must actually be lower, not equal/higher.
    rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    assert rank[SEVERITY_STEP_DOWN["warning"]] < rank["high"]
    assert rank[SEVERITY_STEP_DOWN["critical"]] < rank["critical"]


def test_r2_min_within_unit_range() -> None:
    assert 0.0 < R2_MIN < 1.0
