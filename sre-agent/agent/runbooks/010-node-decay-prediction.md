# Runbook 010 — Forecasted node decay

**Tags:** forecasting, capacity, predictive, node-pressure

## Symptom

The forecaster emits `infra.events.forecasted.node_memory_available_bytes.warning` (or `.critical`), or `forecasted.node_fs_avail_bytes.*`, or `forecasted.node_load1.*`. Projected node-level resource at t+5 min would breach the danger threshold — but the kubelet hasn't emitted a reactive `NodeMemoryPressure` / `DiskPressure` / `Unhealthy` event yet. No pods are evicted yet; no eviction cascade has started.

The signal is: "this node, untouched, will start evicting pods in roughly five minutes." Differs from Runbook 004 (`node-pressure`) which fires AFTER the kubelet has flagged the node — that path is reactive cleanup; this is proactive prevention.

## Diagnosis

Three node-decay patterns drive a forecasted event:

1. **Slow memory leak across all workloads** — `node_memory_available_bytes` climbs DOWN linearly over hours. `r2` ≥ 0.85 with a negative slope. Common when a memory-leaking image was rolled out to multiple Deployments simultaneously.
2. **Disk fill** — `node_fs_avail_bytes` climbing down. Common drivers are growing logs nobody rotates, a container with a stuck loop writing to its writable layer, or a PVC misconfiguration.
3. **Load piling up** — `node_load1` climbing up faster than CPU-bound workloads can drain it. Often a slow-to-start container that retries forever, or a pathological cron job.

The diagnose node should also check whether the affected node is the only one in the cluster (k3d laptop case) — if so, the remediation set is narrower because there's no other node to drain to.

## Remediation

The proactive ladder:

1. **`cordon_node`** — first choice when there's at least one other node available. Stops new scheduling on the decaying node; existing pods stay running. Buys time without disruption. Reversible via `uncordon_node`.

2. **`evict_workload`** (with a label selector targeting the noisiest pod on the node) — when the forecaster identifies one workload as the dominant driver. Soft restart; the scheduler reschedules on the same node (or a different one if the cordon's already in place).

3. **`drain_node`** — only at high/critical severity AND only when at least one healthy node remains. Full evacuation; pods land elsewhere. NOT appropriate for single-node clusters (would brick the cluster) — the diagnose node should detect that and choose `page_human` instead.

4. **`set_resources`** with lowered `requests` on over-provisioned workloads — useful for the slow-leak case where the LLM identifies one Deployment as over-allocating. Frees up scheduler capacity for the displaced pods.

5. **`page_human`** — for single-node clusters, low `r2`, or when the projected breach lands far in the future (t+60min) and a human review is preferable to autonomous capacity changes.

NEVER use `restart_pod` for a forecasted node-decay event — restarting one pod won't fix node-level resource exhaustion.
