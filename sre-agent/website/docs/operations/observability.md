---
id: observability
slug: /operations/observability
title: Observability — metrics & logs
sidebar_position: 1
---

# Observability

The Keos SRE Agent exposes both **Prometheus metrics** and **structured JSON
logs** so the agent itself can be operated like any other production service —
alerted on, dashboarded, log-shipped.

## TL;DR

- Metrics: `GET http://<api>/metrics` (also at `/api/metrics`). No auth, no
  rate limit.
- Logs: stderr, JSON by default in containers, text by default on the laptop.
- Helm: `--set monitoring.enabled=true` renders a `ServiceMonitor` CR for
  the Prometheus Operator.

---

## Metrics catalogue

Every counter / histogram / gauge is declared in `agent/metrics.py`.
Cardinality is kept low on purpose — no incident IDs, no pod names, no
namespaces as label values. Those go in logs and the audit chain.

### Agent loop

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `keos_graph_runs_total` | Counter | `outcome`, `tool` | Total graph runs partitioned by outcome (`healed` / `escalated` / `auto_resolved` / `failed` / `crashed`) and the proposed tool (`restart_pod` / … / `none`). |
| `keos_graph_run_duration_seconds` | Histogram | — | End-to-end graph run latency. Buckets: 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600 s. |
| `keos_llm_call_duration_seconds` | Histogram | `step` | Per-call Ollama duration, partitioned by graph step (`diagnose` / `plan_action`). |

### Safety mesh

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `keos_policy_decisions_total` | Counter | `decision`, `action`, `severity` | OPA evaluations. `decision` ∈ {allow, deny, error}. A rising `deny` rate means the agent's plans don't match the matrix. |
| `keos_escalations_total` | Counter | `reason` | Times the graph terminated via `escalate`. `reason` ∈ {low_confidence, mode_denial, matrix_denial, execute_failed, not_healed, stale_resolved, other}. |
| `keos_pending_approvals` | Gauge | — | Current count of L3/L4-denied actions waiting for operator approval. |
| `keos_agent_mode_info` | Gauge | `mode` | 1 for the active mode (observe-only / active / frozen), 0 for the others. Stack 3 series in Grafana to see the kill-switch state over time. |

### Event bus

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `keos_chaos_observer_events_total` | Counter | `reason` | K8s events the observer translated to NATS. `reason` matches the kubelet event reason (OOMKilled, BackOff, ImagePullBackOff, FailedScheduling, …). |
| `keos_chaos_observer_errors_total` | Counter | `kind` | Stream / publish errors. `kind` ∈ {rate_limited, publish_failed}. Any non-zero rate means the agent is missing events. |
| `keos_nats_consumer_pending` | Gauge | `lane` | Messages waiting in JetStream per consumer lane (`user` / `observer`). Rising = the agent can't keep up. |
| `keos_nats_consumer_in_flight` | Gauge | `lane` | Messages currently in-flight (delivered but not yet acked). |

---

## Suggested alerts

```yaml
groups:
  - name: keos
    rules:
      # Anything that escalated to a human in the last 15 min is worth a
      # ticket. The audit chain has the detail.
      - alert: KeosEscalationFiring
        expr: increase(keos_escalations_total[15m]) > 0
        for: 1m
        labels: { severity: warning }
        annotations:
          summary: "Keos escalated {{ $value }} incident(s) in the last 15 min"
          description: "Reason: {{ $labels.reason }}. Check the Pending Approvals view."

      # Backlog growing means CPU-LLM can't keep up; consider a smaller
      # model or a GPU.
      - alert: KeosConsumerBacklog
        expr: keos_nats_consumer_pending{lane="observer"} > 50
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Keos observer lane backlog > 50 for 10 min"

      # Kill-switch was flipped to frozen.
      - alert: KeosAgentFrozen
        expr: keos_agent_mode_info{mode="frozen"} == 1
        for: 1m
        labels: { severity: info }
        annotations:
          summary: "Keos agent is in frozen mode"
          description: "All actions denied at the OPA gate. Investigate why."

      # Continuous OPA failures = fail-closed → no remediation possible.
      - alert: KeosOpaFailing
        expr: rate(keos_policy_decisions_total{decision="error"}[5m]) > 0.1
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "OPA evaluations failing at > 0.1/s for 5 min"

      # Persistent observer publish failures = events missed.
      - alert: KeosChaosObserverErrors
        expr: rate(keos_chaos_observer_errors_total[5m]) > 0
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "chaos_observer error rate {{ $value }}/s for 5 min"
```

## Sample Grafana panels

A starter dashboard JSON is **not** shipped (every team's Grafana org is
different) but these queries cover the canonical "is the agent healthy"
view:

- **Outcome mix** (stacked area): `sum by (outcome) (increase(keos_graph_runs_total[5m]))`
- **Graph run p95**: `histogram_quantile(0.95, sum(rate(keos_graph_run_duration_seconds_bucket[5m])) by (le))`
- **Diagnose latency p95**: `histogram_quantile(0.95, sum(rate(keos_llm_call_duration_seconds_bucket{step="diagnose"}[5m])) by (le))`
- **Policy decisions** (stacked bar): `sum by (decision) (increase(keos_policy_decisions_total[5m]))`
- **Queue depth**: `keos_nats_consumer_pending`

---

## Structured logging

By default, the agent emits **JSON logs** in containers and **plain text**
on the laptop. Override with `LOG_FORMAT=json|text`.

### JSON shape

Each line is a single JSON object:

```json
{
  "ts":     "2026-06-01T13:42:07.412Z",
  "level":  "INFO",
  "logger": "agent.graph",
  "msg":    "triage: skipping stale event for workloads/nginx-demo-xyz (age=1233s, why=already_ready)"
}
```

If a log call passes `extra={"k": "v"}`, those keys are merged in at the top
level. Example call:

```python
log.warning("rate-limit blocked", extra={"path": "/api/incidents", "key": "203.0.113.5"})
```

### Loki LogQL recipes

```logql
# Every escalation across the cluster.
{app="api"} | json | logger="agent.graph" |~ "escalate:"

# Rate-limit blocks by IP.
{app="api"} | json | logger="agent.api" |~ "rate-limit .* blocked"

# Graph runs longer than 5 min.
{app="api"} | json | logger="serve" |~ "graph run done"
  | unwrap elapsed_ms | __error__="" | > 300000

# A specific incident's full audit-like trail (use the WS channel if you
# want this in near-real-time instead).
{app="api"} | json |~ "inc-1234abcd"
```

### Datadog / OpenSearch

Both pick up the JSON automatically. Map `ts → @timestamp`, `level → status`.

---

## Wiring up Prometheus

### With the Prometheus Operator

```bash
helm upgrade --install pocteo deploy/helm/pocteo \
  --set monitoring.enabled=true \
  --set-string monitoring.labels.release=kube-prometheus-stack
```

The label needs to match your Prometheus's `serviceMonitorSelector`.
Inspect the running Prometheus CR if you're not sure:

```bash
kubectl get prometheus -A -o yaml | grep -A2 serviceMonitorSelector
```

### Without the operator (vanilla Prometheus)

Add a `scrape_config` to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: keos-api
    metrics_path: /metrics
    static_configs:
      - targets: ["api.pocteo.svc.cluster.local:8000"]
    scrape_interval: 30s
```

---

## Where the metrics are emitted

| Metric | Emitting code |
|---|---|
| `keos_graph_runs_total`, `keos_graph_run_duration_seconds` | `agent/serve.py::_process_message` finally block |
| `keos_llm_call_duration_seconds` | `agent/pipeline_trace.py` (only for `diagnose` / `plan_action` nodes) |
| `keos_policy_decisions_total` | `agent/policy_gate.py::_record_decision` |
| `keos_escalations_total` | `agent/graph.py::node_escalate` reason-classification block |
| `keos_pending_approvals` | `agent/api.py::_metrics_response` (sampled on each scrape) |
| `keos_chaos_observer_*` | `agent/chaos_observer.py::_handle` |
| `keos_nats_consumer_*` | `agent/serve.py::_sample_queue_depth` (15 s tick) |
| `keos_agent_mode_info` | `agent/api.py::_metrics_response` (sampled on each scrape) |

The pattern everywhere: a `try / except Exception: pass` wraps every
metric update so a misconfigured Prometheus client never breaks the
graph. Production code paths never raise from a metrics line.
