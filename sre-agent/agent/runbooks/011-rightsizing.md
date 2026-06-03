# Runbook 011 — Rightsizing (chronic over/under-provisioning)

**Tags:** forecasting, rightsizing, efficiency

## Symptom

A workload's baseline + forecast tell a story the reactive layer can't see. Two flavours:

* **Over-provisioned.** `container_memory_rss_bytes` baseline mean is ≤ 30 % of the configured limit, sustained over several days. No spikes. The container has more headroom than it ever uses; the scheduler is reserving capacity nobody needs. The forecaster may not have published an event for this yet — it's a "low and stable", not a breach — but the diagnose node sees the σ-above-baseline at near zero AND the runbook hit on rightsizing.
* **Under-provisioned.** Baseline mean is ≥ 80 % of the configured limit, with frequent excursions above. Forecasted `container_memory_rss_bytes` would breach the limit at t+15min or t+60min. Each excursion is a near-OOM; one out-of-bounds spike and the kubelet evicts the pod.

The signature in both cases is a STABLE divergence between configured limits and observed usage — not a transient spike.

## Diagnosis

Compute headroom = `1 - (baseline.mean / configured_limit)`. Headroom ≥ 0.7 means over-provisioned (cut requests). Headroom ≤ 0.2 means under-provisioned (raise limits). Anything in between is well-sized; no action.

Cross-reference the diagnostic-signals analyzer's findings — if the analyzer also flags `OOMKilled` historical events for this container, the under-provisioned case is confirmed and urgent. If the analyzer flags `Evicted` for neighbouring pods on the same node, the over-provisioned case is robbing scheduler capacity from other workloads.

The forecast slope tells the agent how aggressive to be: a slowly-rising under-provisioned series can tolerate a gradual `set_resources` step; a fast-rising one needs an immediate one.

## Remediation

* **Over-provisioned:** `set_resources` with LOWER `requests`. Limits stay the same (avoid pulling the rug from a hot pod). Recovers scheduler capacity for the cluster. Pair with `tune_hpa` to lower `min_replicas` if the workload is also HPA-managed.

* **Under-provisioned with no OOM history:** `set_resources` with HIGHER `limits`. Requests usually stay the same — what's missing is headroom for the spikes, not steady-state capacity. Sustained near-limit usage AFTER the bump means the workload genuinely needs more — `tune_hpa` to scale out instead, or `page_human` to flag for capacity planning.

* **Under-provisioned WITH OOM history:** Same as above PLUS pair with `tune_hpa` to widen `max_replicas`. Multiple pods at limit means horizontal scaling buys headroom faster than vertical.

* **Page when:** the workload is in a protected namespace, or the diagnose layer can't confidently choose direction, or the rightsizing decision would move requests/limits more than 50 % in one step.

Rightsizing is the SLOW lever — never use `evict_workload`, `restart_pod`, or `rollout_undo` for it. Wait for the next steady-state and re-measure.
