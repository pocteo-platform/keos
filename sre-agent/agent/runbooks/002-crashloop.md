# Runbook 002 — Pod in CrashLoopBackOff

**Tags:** availability, deployment-regression

## Symptom

A pod's container repeatedly exits non-zero shortly after start, and the kubelet backs off restarts with `CrashLoopBackOff`. `kubectl describe pod` shows `Last State: Terminated, Exit Code: 1` (or 137/139 for OOM/segfault) with restart counts climbing. The pod's Deployment usually has at least one other healthy replica, so the user-visible impact may be partial. Cluster signals publish this when the container has crashed three or more times within five minutes.

## Diagnosis

The dominant causes, in order of frequency on this PoC's workloads: (1) a bad image tag or image pull failure that satisfies the API but fails at runtime; (2) a recent Deployment update introduced a config or command error — check whether the Deployment's `revision` annotation changed within the past 30 minutes; (3) the container is OOMKilled (exit 137) because resource limits are too tight; (4) a downstream dependency (DB, message bus) the container needs at boot is unavailable. Pull the last 200 lines of container logs and the pod's events before deciding. Crucially: distinguish a bad *deploy* from a bad *node* — if other replicas of the same Deployment are healthy, the deploy is fine and the issue is replica-local; if all replicas crash identically, the deploy is the problem.

## Remediation

If a recent rollout is implicated (case 2), `rollout_undo` on the Deployment reverts to the previous ReplicaSet — this is reversible and fast. If the crashes are replica-local with no recent deploy (case 1, 3, or 4 with the dependency now healthy), `restart_pod` is sufficient. If the issue is dependency unavailability (case 4) and the dependency is still down, do NOT restart — the new pod will crash too; `page_human` instead so an operator can fix the dependency. For OOMKilled with no recent deploy (case 3 alone), `scale_deployment` to spread load before raising limits, then `page_human` so an operator can re-tune.
