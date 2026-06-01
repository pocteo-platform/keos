# Runbook 004 — Node under resource pressure

**Tags:** node-health, resource-exhaustion, capacity

## Symptom

A node reports `NotReady` or fires kubelet conditions `MemoryPressure=True`, `DiskPressure=True`, or `PIDPressure=True`. Pods are being evicted (`Status: Failed` with `Reason: Evicted`), or new pods cannot schedule (`FailedScheduling: 0/N nodes are available: insufficient memory/disk`). For DiskPressure specifically, look at `imagefs` vs `nodefs` — full image storage manifests differently from full root disk. CPU is rarely "pressure" in the kubelet sense (no `CPUPressure` condition); CPU saturation shows up as throttling on individual containers, not as a node condition.

## Diagnosis

In order of frequency: (1) **a noisy-neighbour workload** is consuming more resources than its requests allow — often a recent deploy with no limits, or a workload whose limits are higher than its requests so the scheduler oversold; (2) **disk pressure from log accumulation** — emptyDir or hostPath volumes filled by an application that didn't rotate logs; (3) **disk pressure from image cache** — too many image versions accumulated, especially common on long-lived nodes with frequent rollouts; (4) **the node itself is undersized** for its current pod assignment — usually a cluster-autoscaling lag. Look at the node's allocatable vs used for each resource. The diagnostic-signals analyzer surfaces the specific condition in its `error` field.

## Remediation

For an acute pressure event affecting workload availability, `cordon_node` immediately to stop new scheduling onto the affected node — this is fast and reversible. Follow with `drain_node` to evict pods gracefully onto healthier nodes; the scheduler reschedules them. Do this only after confirming the rest of the cluster has the capacity to absorb the load (check `kubectl top nodes`). If capacity is borderline, `scale_deployment` on the noisy-neighbour Deployment to reduce its per-pod footprint, OR `page_human` so an operator can raise cluster capacity. Never `restart_pod` on an evicted pod — the scheduler will retry placement and we want it to land on a different node, not the same pressured one.
