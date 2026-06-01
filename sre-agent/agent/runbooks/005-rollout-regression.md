# Runbook 005 — Bad rollout (new revision introduces regression)

**Tags:** rollout-regression, availability, supply-chain

## Symptom

A Deployment's rolling update has begun but the new ReplicaSet's pods are failing to become Ready — readiness probe failures, immediate exits, or steady-state errors visible in logs. Old pods are still serving traffic (because `maxUnavailable` hasn't been breached), but the rollout is stuck and the `rollout status` command reports `Waiting for deployment "X" rollout to finish`. Telemetry may also surface elevated error rates from the canary subset before the operator notices the rollout is stalled.

## Diagnosis

The four common roots: (1) **a config-map or secret change** baked into the new image that diverges from what the running pods expect — environment misconfig; (2) **a schema change in a downstream system** (database migration not yet applied) that breaks the new code path; (3) **a code regression** that escaped staging — null pointer, init order, library version mismatch; (4) **resource requests/limits changed** in the new revision and the new pods can't schedule. Compare the failing ReplicaSet's spec to the previous one. The diagnostic-signals analyzer reliably flags rollout-stuck conditions; the LLM should reconcile that with the falco-style event's claim.

## Remediation

The default is `rollout_undo` — revert to the previous good ReplicaSet. It's fast, reversible, and avoids the much harder question of WHICH of the four root causes you're facing. If the rollout has already moved enough traffic that undoing is destructive (rare, but possible with stateful workloads), `page_human` instead. Never `restart_pod` on a failing replica from the new ReplicaSet — the new pod will hit the same regression. `scale_deployment` is irrelevant: more bad replicas don't help.
