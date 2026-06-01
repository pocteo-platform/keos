# Runbook 007 — Traffic surge / hot pod under load

**Tags:** capacity, scaling, performance

## Symptom

A subset of pods in a Deployment shows elevated request latency, CPU throttling, or error-rate increase, while the rest of the same Deployment is fine. Distinct from a regression (all replicas degrade equally) — the signature is *hot pod* asymmetry: a few replicas at 95%+ CPU while others are at 30%. Often correlated with a planned marketing event, a viral spike, or a botted product page. The HPA may already be slowly scaling but lags behind the demand curve. Telemetry shows requests/sec climbing faster than HPA's `desiredReplicas` is moving.

## Diagnosis

The three common roots: (1) **legitimate traffic surge** exceeding the HPA's response time — HPA reacts on 30-second windows by default, so a fast spike outruns it; (2) **broken load balancing** — a session-affinity bug or an upstream caching layer is funneling all traffic to a subset of pods regardless of the others' availability; (3) **a noisy customer / abuse** — one tenant's traffic dominates. Differentiate case 1 from case 2 by checking whether `Service` endpoints are balanced and whether request counts across pods are uniform. The diagnostic-signals analyzer can confirm pod-level imbalance.

## Remediation

For case 1 (real surge), `scale_deployment` to bump replicas ahead of HPA — this is fast and reversible; HPA will then settle back to its computed value. For case 2 (LB bug), `restart_pod` on the over-loaded pods to drop their session-affinity cookies, then `page_human` so an operator can fix the LB config. For case 3 (abuse), `page_human` — the action belongs to the platform's rate-limiting / WAF layer, not the workload tier. Never `rollout_undo` for traffic events — the deploy is innocent. Never `cordon_node` either — the issue is workload-level, not node-level.
