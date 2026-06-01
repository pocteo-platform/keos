# Runbook 008 — Pods stuck Pending: FailedScheduling

**Tags:** scheduling, cordon, uncordon, node-state

## Symptom

One or more pods stay in the `Pending` phase. `kubectl describe pod` shows:

```
Status:           Pending
Conditions:
  Type           Status
  PodScheduled   False
```

Recent events include `Warning FailedScheduling … nodes are unschedulable`
or `… no nodes available`.

## Diagnosis

The scheduler cannot place the pod. The most common single-node-cluster
cause is **a previously cordoned node** that nobody re-enabled:

- Earlier `drain_node` or `cordon_node` ran successfully.
- The node is marked `SchedulingDisabled` (taint
  `node.kubernetes.io/unschedulable:NoSchedule`).
- New ReplicaSet rolls out → new pods are created → scheduler has nowhere
  to place them → `Pending` forever.

Less common causes (page human if these apply): genuine resource
exhaustion, hard node-affinity rules with no matching node, PVC waiting
on a missing StorageClass.

## Remediation

If the node is healthy again, **`uncordon_node`** on the node whose
status is `Ready,SchedulingDisabled`. This is the inverse of cordon —
zero blast radius, just re-enables placement. The matrix allows
`uncordon_node` at every severity.

After uncordoning, expect:

- `FailedScheduling` events stop firing within ~10 s.
- The `Pending` pods transition to `ContainerCreating` then `Running`.
- `kubectl get nodes` shows the node as just `Ready` (no `SchedulingDisabled`).

If pods stay Pending after uncordon, the cause is not cordon-related —
escalate to `page_human` so an operator can investigate node resource
limits / affinity / storage.
