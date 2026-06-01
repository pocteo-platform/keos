# Runbook 001 — Container memory leak / OOM trajectory

**Tags:** memory, resource-exhaustion, rollout-regression

## Symptom

A container's working-set memory (RSS) climbs monotonically across a wall-clock window of 5–30 minutes, well past any normal warm-up plateau. Telemetry shows a positive linear or super-linear slope in `container_memory_working_set_bytes`. The kubelet has not yet OOMKilled the container, but eviction is imminent — depending on the limit, ETA can be predicted from the slope (`(limit - current) / d/dt`). Often correlated with a recent Deployment revision. May be accompanied by GC pressure metrics for JVM/Go workloads.

## Diagnosis

The four common roots, in rough order of frequency on this PoC's workloads: (1) **a regression in a recent rollout** — the new image leaks or holds references the previous one freed; check the Deployment's `revision` annotation and image SHA against what was running 30 minutes ago; (2) **resource limit set too tight** for the workload's legitimate working set — common after a marketing campaign or product launch shifts the traffic profile but limits were not re-tuned; (3) **a downstream dependency change** that causes the workload to buffer (e.g., a slower database forcing in-memory queues to grow); (4) **a real bug** in the running image not caught in staging. Cross-reference the trajectory with the rollout timeline: if memory grew from baseline immediately after a deploy, treat as regression; if it grew gradually over hours/days, treat as capacity.

## Remediation

For a regression (root cause 1), `rollout_undo` on the Deployment reverts to the prior ReplicaSet whose memory was stable — fastest path back to a healthy baseline, fully reversible. For a capacity issue (root cause 2), `scale_deployment` to add replicas and reduce per-pod working-set headroom is the safer move than just raising limits; raising limits is an operator decision and should be `page_human`. For dependency-driven buffering (root cause 3), `restart_pod` clears the in-memory queue but only buys time — `page_human` once you've stabilised so the underlying dependency can be fixed. Never restart blindly without checking the trajectory direction post-restart: if memory immediately climbs again, the regression hypothesis is correct and you should `rollout_undo`.
