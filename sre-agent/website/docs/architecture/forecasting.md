---
sidebar_position: 6
title: Predictive forecasting (Phase 14)
---

# Predictive forecasting

Phase 14 closes the proactive half of the **Observe → Predict → Heal
→ Learn** loop. Where Phase 13's baselines give the diagnose LLM a
"this metric is N σ above its 7-day mean" snapshot, Phase 14 takes
the next step: "this metric is climbing fast enough that at t+5 min
it will be 12 σ above the mean — act now."

Result: the agent acts BEFORE the reactive kubelet event fires, not
after. Expected confidence-lift on top of Phase 13: +3 to +5 pp on
the rules where short-horizon prediction beats reactive
post-hoc remediation (memory leaks, traffic surges, slow-disk-fill).

## What the agent does

Three pieces, all in `agent/forecasting/`:

### 1. Linear-regression forecaster (`forecaster.py`)

Pure-Python ordinary least squares over the last N
`(timestamp, value)` samples for each fitted `MetricKey` in the
Phase 13 baseline store. Returns slope, intercept, R², and three
horizons: **5 min, 15 min, 60 min**.

Why linear? Two reasons:

1. **It works at the horizons we care about.** Short-horizon CPU and
   memory trends are dominated by their local slope. Exponential growth
   (a real leak) looks linear over a 5-minute window. The cases where
   linear is clearly wrong (seasonal load patterns, autoscaling
   feedback) are *exactly* the cases where R² drops below the
   `R2_MIN` floor and the forecast is discarded.
2. **The math is cheap.** 60 samples × an OLS pass = ~10 microseconds.
   Holt-Winters or ARIMA would be slower and would pull `statsmodels`
   into the agent's container — a 50 MB+ dependency tax for marginal
   accuracy at the horizons we use.

Critical implementation detail: **timestamps are centered before the
OLS sums**. Unix-epoch timestamps (~1.7e9 in 2026) make `Σt²` ≈ 3e18,
and `Σt² − n·mean_t²` collapses to noise in float64. Centering the
samples (subtract `mean_t`) keeps the magnitudes small and the
arithmetic exact. The first iteration of this code skipped the
centering and silently produced slope ≈ 0 on every real series —
the regression test for this lives in `tests/test_forecasting.py`.

### 2. Background scheduler (`scheduler.py`)

Asyncio task started by `agent.serve` (only when `PROMETHEUS_URL` is
set — same gate as the baseline learner). Tick cadence default 60 s.

On each tick:

1. Walk every `MetricKey` the baseline store knows about.
2. For each one with a fitted baseline (mean + std), fit a fresh
   linear regression over its 60 most-recent samples.
3. Classify the 5-min forecast against the baseline via `decide()`:

   | σ above mean | Level | Action |
   |---|---|---|
   | ≥ 2 × Z_TRIGGER (10 σ) | `critical` | Publish event |
   | ≥ Z_TRIGGER (5 σ) | `warning` | Publish event |
   | ≥ Z_TRIGGER / 2 (2.5 σ) | `watch` | Log only |
   | else | `safe` | No-op |

4. Publish warning / critical events to NATS as
   `infra.events.forecasted.<metric>.<level>`. The agent's existing
   JetStream consumer picks them up like any reactive event.

### 3. Diagnose-time enrichment (`agent/graph.py`)

The Phase 13 `enrich_with_baselines` node was extended (not
replaced): each anomalous baseline snapshot now also carries a
`forecast` sub-dict with the slope, R², and the three horizon
projections. The diagnose user prompt renders them as:

```
=== METRIC BASELINES (target pod) ===
- container_memory_rss_bytes: current 838 MiB, baseline 412 MiB ± 18 MiB
  (+23.6σ above, n=942)
    forecast: t+5min → 980 MiB (level=critical, r²=0.94, slope/s=+1.5e6)
- container_cpu_rate_5m: current 0.83, baseline 0.21 ± 0.04
  (+15.5σ above, n=942)
```

The LLM is told (in `DIAGNOSE_SYSTEM`) to weight σ-above-baseline
more than raw values. The forecast line tells it which direction
the metric is moving and how fast — the difference between "act
now" and "wait, this is plateauing."

## Severity step-down

False forecasts can flood operators with predictions that never
materialise. The plan's risk register names this as the
forecaster's #1 failure mode.

Mitigation: every forecasted event publishes at **one severity step
LOWER** than the equivalent reactive event:

| Forecast level | Published severity |
|---|---|
| `warning` | `medium` |
| `critical` | `high` |

OPA's matrix at the lower severity is more conservative — actions
that would be auto-allowed at the reactive severity often require
operator approval at the forecasted severity. The cost of a false
positive is one extra approval prompt; the cost of a missed
forecast is the reactive event fires and Runbook 004 / 007 takes
over. Both paths are safe.

Tunable via `SEVERITY_STEP_DOWN` in `agent/forecasting/scheduler.py`.

## Three new tools (Phase 14b)

The forecaster's events drive choices the existing tool catalog
couldn't act on. Three new actions land in `agent/tools.py`:

| Tool | Args | Use |
|---|---|---|
| `tune_hpa` | `hpa_name`, `namespace`, optional `min_replicas`, `max_replicas`, `target_cpu` | Raise HPA ceiling before a forecasted surge; lower target_cpu for a more aggressive auto-scale. |
| `set_resources` | `deployment_name`, `namespace`, `container`, optional `requests`, `limits` | Raise memory limits before a forecasted OOM; lower requests for rightsizing. |
| `evict_workload` | `namespace`, `label_selector` | Soft restart of noisy pods so the scheduler reschedules them. Empty selector is rejected. |

OPA matrix (per Phase 14b):

```
tune_hpa        low=3 med=2 high=1 critical=1
set_resources   low=3 med=2 high=1 critical=1
evict_workload  low=4 med=3 high=2 critical=1
```

All three are reversible in the audit-chain sense (the operator
can apply the inverse patch). `evict_workload` only loosely so —
the new pods are different identities.

## Three new runbooks (Phase 14c)

* **`009-traffic-forecast.md`** — short-horizon CPU/network climb.
  Preferred ladder: `tune_hpa` → `scale_deployment` → `set_resources`
  → `page_human`. Never `restart_pod` or `rollout_undo` (the breach
  hasn't happened yet).
* **`010-node-decay-prediction.md`** — slow memory leak / disk fill
  across all workloads on a node. Preferred ladder: `cordon_node` →
  `evict_workload` → `drain_node` → `set_resources` →
  `page_human`. Explicit warning against draining a single-node
  cluster.
* **`011-rightsizing.md`** — chronic over/under-provisioning.
  Headroom math (`1 − baseline.mean / configured_limit`); over →
  cut requests; under → raise limits and optionally widen HPA.
  Cross-references the diagnostic analyzer for OOM history.

## Observability

Three new Prometheus families on the agent's `/metrics`:

* `keos_forecast_events_total{metric, level}` — publish counter.
  Flat in the presence of a known load ramp → tune
  `Z_TRIGGER_FORECAST` down.
* `keos_forecast_sigma_above` — distribution of σ at the 5-min
  horizon, per published event.
* `keos_forecast_r2` — fit-quality distribution. Persistent
  left-shift toward R² ≈ 0.5 indicates the linear model is
  fighting non-linear behaviour and Holt-Winters becomes worth the
  dependency cost.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `FORECAST_TICK_INTERVAL_S` | `60` | Scheduler tick cadence. Clamped to ≥ 30 s. |
| `FORECAST_FIT_WINDOW` | `60` | Number of recent samples fed to the OLS pass per tick. |
| `FORECAST_DEDUP_WINDOW_S` | `300` | Cooldown after a publish for the same `(metric, level)` pair. |

The scheduler is gated on `PROMETHEUS_URL` being set — empty string
disables both the Phase 13 learner and the Phase 14 scheduler, with
one INFO log.

## Cold-start behaviour

Inherits the Phase 13 cold-start: until a `MetricKey` has ≥ 50
samples *and* a fitted baseline (~50 minutes at the default scrape
cadence), the forecaster has nothing to fit and the scheduler
silently skips it. The diagnose prompt falls back to its
pre-Phase-14 shape — same as if forecasting were disabled.

## Implementation notes

* The scheduler's dedup is **in-memory only**. A pod restart
  clears it and re-publishes — the right safety default when the
  agent was paused for a reason. Operators who want persistence
  across restarts can move the cooldown dict to the SQLite store
  (Phase 16 candidate).
* Per-key errors are caught and logged at WARNING. One bad key
  doesn't poison the loop — the tick continues with the rest.
* The diagnose-time forecast uses the SAME 60-sample window as the
  scheduler, so a forecast the operator sees in an event payload
  matches what the LLM sees in the prompt at incident time.
