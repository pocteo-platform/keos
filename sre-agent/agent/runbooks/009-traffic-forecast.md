# Runbook 009 — Forecasted traffic surge

**Tags:** forecasting, scaling, predictive

## Symptom

The forecaster (Phase 14) emits `infra.events.forecasted.container_cpu_rate_5m.warning` (or `.critical`) when a Deployment's 5-min projected CPU rate would land more than `Z_TRIGGER_FORECAST` σ above its 7-day baseline. The reactive event has NOT fired yet — pods are still healthy, the HPA's `desiredReplicas` hasn't moved, no degradation is visible to users. The event is the predictor saying: "in roughly five minutes, the workload will be in trouble unless we act now."

Distinct from the reactive Runbook 007 (`traffic-surge`) — that one fires AFTER hot-pod asymmetry shows up. This one fires BEFORE, and acts before users notice.

## Diagnosis

The forecast carries three context fields the LLM should weight before choosing an action: `slope_per_s` (how fast the metric is climbing per second), `r2` (linear-fit quality 0..1), and `sigma_above` (5-min σ above the EWMA mean). High `r2` (≥ 0.9) means the linear extrapolation is trustworthy; lower `r2` means the trend is noisier and a gentler intervention is appropriate. If the runbook excerpt mentions a recent deploy revision, treat the forecast with extra caution — a regression that's just landed may look like a surge but doesn't need capacity, it needs rollback.

Differentiate from Runbook 011 (rightsizing) by horizon and direction: traffic-forecast is a SHORT-horizon CPU/network surge climbing UP; rightsizing is a LONG-horizon memory or CPU pattern showing persistent over- OR under-provisioning.

## Remediation

The proactive ladder, in order of operator-preference:

1. **`tune_hpa`** (raise `max_replicas` or lower `target_cpu`) — first choice when the workload already has a HorizontalPodAutoscaler. Widens the autoscaler's ceiling so HPA's natural reactive loop can scale into the predicted demand without bumping into its cap. Reversible by setting back; the audit chain carries both old and new values.

2. **`scale_deployment`** (one-shot replica bump) — for workloads WITHOUT an HPA, or when the predicted surge is steep enough that HPA's 30-s reaction window would lag. Manual headroom; operator (or a follow-up HPA) brings replicas back down later.

3. **`set_resources`** (raise request/limit) — when the diagnose layer determines the bottleneck is per-pod (CPU throttling) rather than aggregate request rate. Pair with `tune_hpa`/`scale_deployment` when both are limiting.

4. **`page_human`** — when `r2` is low (< 0.6), or the linear extrapolation crosses into a value range the runbook says shouldn't be touched autonomously, or the workload is in a protected/critical namespace.

NEVER use `restart_pod` or `rollout_undo` for a forecasted event — the predicted breach hasn't happened yet; there's nothing to restart, and the deploy is innocent.
