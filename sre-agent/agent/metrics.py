"""Prometheus metrics for the Keos agent.

Every counter / histogram / gauge that an SRE actually wants to alert on
is declared here, ONCE. Other modules import the named objects and call
`.inc()` / `.observe()` / `.set()` at the right places. The endpoint
`GET /api/metrics` returns the standard text exposition.

Naming conventions follow Prometheus best practices:

  - `*_total` — monotonic counters.
  - `*_seconds` — durations in seconds, exposed as histograms with
    explicit buckets that cover the agent's actual latency profile
    (LLM calls on CPU = 5-120 s; kubectl calls = 0.05-3 s; HTTP = ms).
  - bare names — gauges (values that go both up and down).

Cardinality is kept low on purpose:
  - `tool` label values are bounded by the `ToolName` literal type (6 + 1).
  - `outcome` is bounded to {healed, escalated, auto_resolved, failed}.
  - `reason` for chaos_observer is bounded by REASON_MAP (~14).
  - `severity` is bounded to {low, medium, high, critical}.

Never add a label whose value space is user-controlled (pod name, namespace,
incident_id) — those go in audit logs, not in a Prometheus time series.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    multiprocess,
)

# Single registry shared across the process. We don't use multiprocess
# (yet) because the agent runs as a single uvicorn worker — when we go
# multi-worker, swap `CollectorRegistry()` for `multiprocess.MultiProcessCollector`.
REGISTRY = CollectorRegistry()


# ---------------------------------------------------------------------------
# Graph counters / histograms — the per-incident lifecycle.
# ---------------------------------------------------------------------------

graph_runs_total = Counter(
    "keos_graph_runs_total",
    "Total agent graph runs, partitioned by outcome, the action that was "
    "ultimately attempted (None when the graph short-circuited at triage), "
    "and the agent domain (sre | security).",
    labelnames=("outcome", "tool", "domain"),
    registry=REGISTRY,
)

graph_run_duration_seconds = Histogram(
    "keos_graph_run_duration_seconds",
    "End-to-end graph run duration. Buckets cover the CPU-LLM range "
    "(seconds to minutes); a real GPU deployment would re-tune these.",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

llm_call_duration_seconds = Histogram(
    "keos_llm_call_duration_seconds",
    "Per-call Ollama duration, partitioned by graph step.",
    labelnames=("step",),  # diagnose | plan_action | warmup
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Calibration — Phase 12. The 90 %-confidence target lives here.
# Cardinality is bounded by the seeded runbook set + macro-loop's
# proposed runbooks (Phase 16) — single-digits to low-tens per cluster.
# ---------------------------------------------------------------------------

diagnose_confidence = Histogram(
    "keos_diagnose_confidence",
    "Distribution of LLM-reported confidence at the diagnose step, "
    "partitioned by rule. Operators alert on a falling p50 (rule's "
    "diagnoses are getting more uncertain) and on a rising "
    "count above the per-rule threshold (rule is finally well-tuned).",
    labelnames=("rule",),
    # Confidence is 0..1; buckets bias to the [0.5, 0.95] range where
    # the threshold decisions actually happen.
    buckets=(0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99),
    registry=REGISTRY,
)

diagnose_outcome_total = Counter(
    "keos_diagnose_outcome_total",
    "Terminal incident outcomes per rule, used by the calibration store "
    "to compute per-rule accuracy. Bucket values match calibration/grader.py.",
    labelnames=("rule", "bucket"),
    # bucket ∈ {healed, escalated_lowconf, escalated_other, stale, override}
    registry=REGISTRY,
)

calibration_threshold = Gauge(
    "keos_calibration_threshold",
    "The threshold currently gating the diagnose→plan_action edge for "
    "each rule. Includes any operator override. Compare with "
    "keos_diagnose_confidence p50 to see if the gate is too tight.",
    labelnames=("rule",),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Baselines — Phase 13. Lets dashboards answer "is the learner running?"
# and "how anomalous was the last incident's target pod?".
# Cardinality stays low: gauges are per metric_name (≤ 10), histogram is
# unlabeled. Per-(metric, pod) detail lives in the SQLite store, not in
# Prometheus.
# ---------------------------------------------------------------------------

baseline_samples_total = Counter(
    "keos_baseline_samples_total",
    "Samples persisted by the baseline learner since startup, labelled by "
    "the logical metric name. A flat counter means the learner has stopped "
    "polling or Prometheus is down.",
    labelnames=("metric",),
    registry=REGISTRY,
)

baseline_keys_known = Gauge(
    "keos_baseline_keys_known",
    "Number of distinct (metric, namespace, scope) keys the store has "
    "seen. Roughly 'how many pods/nodes the agent has learned'.",
    registry=REGISTRY,
)

baseline_enrich_sigma_above = Histogram(
    "keos_baseline_enrich_sigma_above",
    "Per-snapshot σ-above-baseline seen by the enrich_with_baselines node. "
    "p90 ≥ 3 means the incident's target pod looked clearly anomalous; "
    "p90 ≤ 1 means the baselines didn't help separate signal from noise.",
    buckets=(0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 25.0),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Forecasting — Phase 14. The proactive half of the loop. These let
# dashboards answer "is the scheduler running?" + "is it actually
# catching predicted breaches before the reactive event fires?".
# ---------------------------------------------------------------------------

forecast_events_total = Counter(
    "keos_forecast_events_total",
    "Forecasted incident events published to NATS, labelled by metric "
    "(memory / cpu / fs / restarts / …) and danger level. A flat counter "
    "in the presence of a known load ramp means the forecaster is "
    "missing the signal — tune Z_TRIGGER_FORECAST.",
    labelnames=("metric", "level"),  # level ∈ {warning, critical}
    registry=REGISTRY,
)

forecast_sigma_above = Histogram(
    "keos_forecast_sigma_above",
    "Per-event σ-above-baseline at the 5-min horizon. Distribution helps "
    "tune the alert threshold — if p50 = 7σ the threshold is too loose; "
    "if p50 = 25σ it's too tight.",
    buckets=(2.0, 3.0, 5.0, 7.5, 10.0, 15.0, 25.0, 50.0),
    registry=REGISTRY,
)

forecast_r2 = Histogram(
    "keos_forecast_r2",
    "Per-event R² of the linear fit. A persistent left-shift toward "
    "R² ≈ 0.5 means the linear model is fighting non-linear behaviour "
    "(e.g., load spikes, exponential growth). Tracking this is the "
    "input signal for moving to Holt-Winters in a later phase.",
    buckets=(0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 0.99),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# GitOps drift — Phase 15. One counter per K8s kind so dashboards can
# tell at a glance whether drift is concentrated in one resource type
# (frequent restarts on a CronJob, a hot ConfigMap operators keep
# editing live, etc.). Cardinality is bounded by the kinds the SoT
# tracks — single-digit on most clusters.
# ---------------------------------------------------------------------------

drift_events_total = Counter(
    "keos_drift_events_total",
    "Drift events published by the detector, partitioned by K8s kind. "
    "A flat counter in the presence of known drift means the detector "
    "isn't reaching the cluster or its SoT — check DRIFT_SOURCE_TYPE / "
    "DRIFT_SOURCE_PATH and kubeconfig wiring.",
    labelnames=("kind",),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Policy + safety mesh counters.
# ---------------------------------------------------------------------------

policy_decisions_total = Counter(
    "keos_policy_decisions_total",
    "OPA evaluations partitioned by decision, action, severity. Operators "
    "alert on a rising `decision=deny` rate, which signals the agent is "
    "proposing actions the matrix says are too aggressive.",
    labelnames=("decision", "action", "severity"),
    registry=REGISTRY,
)

escalations_total = Counter(
    "keos_escalations_total",
    "Times the graph terminated via the escalate node. `reason` is a "
    "coarse-grained tag (low_confidence | mode_denial | matrix_denial | "
    "execute_failed | not_healed | stale_resolved) — high cardinality stays "
    "in the audit log.",
    labelnames=("reason",),
    registry=REGISTRY,
)

pending_approvals = Gauge(
    "keos_pending_approvals",
    "Current count of pending operator approvals (L3/L4 matrix denials).",
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Observer + bus counters.
# ---------------------------------------------------------------------------

chaos_observer_events_total = Counter(
    "keos_chaos_observer_events_total",
    "K8s events the observer published to NATS, by reason. Cardinality is "
    "bounded by REASON_MAP in agent/chaos_observer.py.",
    labelnames=("reason",),
    registry=REGISTRY,
)

chaos_observer_errors_total = Counter(
    "keos_chaos_observer_errors_total",
    "Stream / publish errors in the observer. A non-zero rate means the agent is missing events.",
    labelnames=("kind",),  # stream_error | publish_failed | rate_limited
    registry=REGISTRY,
)

nats_consumer_pending = Gauge(
    "keos_nats_consumer_pending",
    "Messages waiting in JetStream per consumer lane (user / observer). "
    "Rising = the agent isn't keeping up with event arrival rate.",
    labelnames=("lane",),  # user | observer
    registry=REGISTRY,
)

nats_consumer_in_flight = Gauge(
    "keos_nats_consumer_in_flight",
    "Messages currently being processed (delivered but not yet acked).",
    labelnames=("lane",),
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# Agent mode (informational gauge).
# ---------------------------------------------------------------------------

agent_mode_info = Gauge(
    "keos_agent_mode_info",
    "Current AgentMode CR value. Set to 1 for the active mode, 0 otherwise. "
    "Lets dashboards stack three series (observe-only / active / frozen) "
    "and immediately see when the kill-switch was flipped.",
    labelnames=("mode",),
    registry=REGISTRY,
)


def set_agent_mode(current: str | None) -> None:
    """Project the AgentMode CR value into the gauge family. One series per
    valid mode; only the current one is 1."""
    for m in ("observe-only", "active", "frozen"):
        agent_mode_info.labels(mode=m).set(1.0 if m == current else 0.0)


def render_exposition() -> tuple[bytes, str]:
    """Render the Prometheus exposition payload. Returns (body, content_type).
    Called by the FastAPI `/api/metrics` handler."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


# Silence "unused import" warnings for the multiprocess re-export.
__all__ = [
    "REGISTRY",
    "graph_runs_total",
    "graph_run_duration_seconds",
    "llm_call_duration_seconds",
    "diagnose_confidence",
    "diagnose_outcome_total",
    "calibration_threshold",
    "baseline_samples_total",
    "baseline_keys_known",
    "baseline_enrich_sigma_above",
    "forecast_events_total",
    "forecast_sigma_above",
    "forecast_r2",
    "drift_events_total",
    "policy_decisions_total",
    "escalations_total",
    "pending_approvals",
    "chaos_observer_events_total",
    "chaos_observer_errors_total",
    "nats_consumer_pending",
    "nats_consumer_in_flight",
    "agent_mode_info",
    "set_agent_mode",
    "render_exposition",
    "multiprocess",
]
