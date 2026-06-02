---
sidebar_position: 5
title: ML baselines for cluster metrics
---

# ML baselines for cluster metrics

Phase 13 ships the single biggest confidence lever toward the 90 %
target: every diagnose call now sees a structured "this metric is N σ
above its 7-day baseline" snapshot for the incident's target pod,
alongside the Falco-shaped event, the runbook excerpts, and the
K8sGPT findings.

Anecdotally this is the dominant accuracy lever in operator-LLM
systems. A 3B chat model can argue itself into a wrong diagnosis from
raw values ("memory is 838 MiB — that's a lot"). The same model picks
the right root cause when you tell it "memory is 838 MiB, baseline
412 ± 18 MiB, that's 23 σ above and not a seasonal effect" — because
the prompt has done the comparison for it.

## What the agent does

Three moving pieces. All run inside the same agent process; no extra
deployment, no extra services beyond Prometheus.

### Learner — `agent.baselines.learner`

A background asyncio task started by `agent.serve`. Two cadences:

* **Scrape tick (60 s default).** Issues `/api/v1/query` for each
  configured PromQL series, parses the instant vector, persists one
  `Sample` row per `(metric, namespace, pod_or_node)` into a local
  SQLite database (`data/baselines.db`).
* **Fit tick (600 s default).** Walks every known `MetricKey`,
  reads its recent samples, fits an EWMA mean + rolling stddev plus
  a per-hour-of-day median (for daily seasonality), and writes the
  result back into the `baselines` table. Prunes samples older than
  `BASELINE_RETAIN_DAYS` (14 days).

Both ticks are wrapped in wide try/except. A Prometheus blip, an
unparseable response, an empty vector — each is logged once at
WARNING and the loop keeps going. The graph node falls back to
"no baselines" when the store is empty.

### Default query catalog

Six series cover the incidents the agent diagnoses today:

| Logical metric                  | PromQL source                                                 |
|---------------------------------|---------------------------------------------------------------|
| `container_memory_rss_bytes`    | `sum by (namespace, pod) (container_memory_rss{...})`         |
| `container_cpu_rate_5m`         | `sum by (namespace, pod) (rate(container_cpu_usage…[5m]))`    |
| `kube_pod_restarts_total`       | `sum by (namespace, pod) (kube_pod_container_status_restarts_total)` |
| `node_load1`                    | `node_load1`                                                  |
| `node_memory_available_bytes`   | `node_memory_MemAvailable_bytes`                              |
| `node_fs_avail_bytes`           | `min by (instance) (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"})` |

Operators extend the catalog via `BASELINE_EXTRA_QUERIES_JSON` — a
JSON array of `{metric, promql, namespace_label?, scope_label?}`
objects. No agent rebuild needed; the learner picks up the new
queries the next time it starts.

### Enrich-with-baselines node — `agent.graph.node_enrich_with_baselines`

A new graph node sitting between `k8sgpt_analyze` and `diagnose`.
For each incident:

1. Read `state["namespace"]` + `state["pod"]`.
2. Ask the store for every known `MetricKey` scoped to that pod.
3. For each key, look up the most-recent sample (current value) +
   the fitted baseline, build a `BaselineSnapshot`.
4. Rank by `"anomalous first, then descending |σ-above|"`.
5. Return the top 5 as `state["baselines"]`.

If the pod is unknown, or Prometheus has never been configured, the
node returns `baselines=[]`. The graph keeps going; the diagnose
LLM falls back to raw values.

### Diagnose prompt extension — `agent.llm.diagnose`

The diagnose system prompt now mentions a fourth input source ("metric
baselines for the target pod") and gives the LLM a worked example:
weight an "N σ above baseline" reading more than a raw absolute. The
user prompt grows a `=== METRIC BASELINES (target pod) ===` block
when snapshots exist, formatted human-readably:

```
=== METRIC BASELINES (target pod) ===
- container_memory_rss_bytes: current 838.0 MiB, baseline 412.0 MiB ± 18.0 MiB
  (+23.6σ above, seasonal Δ 426.0 MiB, n=942)
- container_cpu_rate_5m: current 0.83, baseline 0.21 ± 0.04
  (+15.5σ above, n=942)
```

## Status semantics

Each snapshot carries a status field — the LLM is told to treat each
case differently:

| Status      | Meaning                                                                 |
|-------------|-------------------------------------------------------------------------|
| `ok`        | ≥ `BASELINE_MIN_SAMPLES` (50) samples, full baseline. σ-above and (when ≥ 7 days of history) seasonal residual are computed. |
| `learning`  | The store has the metric but fewer than 50 samples. The current value is reported; the LLM is instructed NOT to invent a baseline. |
| `missing`   | Prometheus has never reported a value for the key, or the learner isn't running. |

Status migrations are gentle: the learner refits every 10 minutes, so
a fresh pod becomes "known" within tens of minutes of first appearing.

## Math

Pure Python — no numpy, no statsmodels. The edge target must run on
Jetson-class hardware where every megabyte of dependency tree matters.

### EWMA mean + variance

`agent.baselines.math.ewma_mean_var(values, alpha=0.05)` computes an
exponentially-weighted mean and an EWMA-against-current-mean variance
in a single pass. `alpha=0.05` at the default 60 s scrape gives a
~20-minute effective averaging window — slow enough to ignore single
chaos events, fast enough to track a real load shift within an hour.

### Z-score with σ floor

`safe_zscore(current, mean, std)` returns `(current - mean) / std`
when `std ≥ 1e-6`; **0.0 otherwise**. The floor avoids the
"infinity σ above baseline" failure mode on a flat series with a
micro-perturbation (e.g., memory_rss jittering by 1 KiB).

### Hour-of-day median

`hour_of_day_medians(samples, periods=24)` buckets samples by their
unix-timestamp hour-of-day and returns one median per bucket. Cheap
by construction: O(N) bucketing + O(B·log B) median where B is the
largest bucket size. Requires ≥ `BASELINE_SEASONAL_MIN_SAMPLES` (672)
samples before the seasonal residual is reported — that's seven days
of one-per-15-minute history.

## Operator surface

### `GET /api/baselines`

Without filters: every known `MetricKey` returned as a `status=ok`
or `status=missing` placeholder. Use the SPA's "what is the learner
seeing?" page or as a sanity check during deployment.

```bash
$ curl -H "Authorization: Bearer $SHP_API_TOKEN" \
       http://localhost/api/baselines | jq '.keys_known, .total_samples'
12
1843
```

### `GET /api/baselines?ns=workloads&pod=nginx-demo-7b...`

Returns the same top-5 snapshots the `enrich_with_baselines` node
would inject into the diagnose prompt. Useful when debugging a
specific incident's diagnose: "what did the LLM actually see?"

### `make doctor`

Adds a `BASELINES` line:

```
[OK]  BASELINES    keys=12 samples=1843 fitted=8 learning=4
```

`fitted` = baselines with `status=ok`. `learning` = baselines with
fewer than the seasonal threshold of samples. A `fitted` of zero
after several minutes of `make up` means the learner hasn't reached
Prometheus — check `PROMETHEUS_URL` and the compose logs of the
`api` container.

### Prometheus exposition

The agent's own `/metrics` endpoint gains three new families:

* `keos_baseline_samples_total{metric}` — counter, samples persisted
  since startup. A flat counter means the learner stopped polling.
* `keos_baseline_keys_known` — gauge, distinct `MetricKey` count.
* `keos_baseline_enrich_sigma_above` — histogram, per-snapshot
  σ-above-baseline observed by the diagnose node. p90 ≥ 3 means the
  baselines are separating signal from noise well; p90 ≤ 1 means the
  catalog is mismatched to what the cluster actually does.

## Cold-start behaviour

A fresh edge install has no baselines for the first hour. Concretely:

* Within ~60 s: the learner has finished its first scrape; the
  doctor reports `keys=N samples=N` with `fitted=0 learning=N`.
* Between ~5 and ~50 minutes: each key passes the
  `BASELINE_MIN_SAMPLES` threshold (depending on its scrape result
  rate). The next fit tick promotes them to `status=ok`. σ-above is
  meaningful from this point; seasonal residual is still `None`.
* After 7 days: hour-of-day buckets accumulate enough samples that
  `hour_of_day_medians` returns non-`None` entries. Seasonal residual
  appears in snapshots.

During the cold-start window the LLM falls back to raw values,
exactly as it did before Phase 13. There is no degradation —
just the absence of the lift.

## Configuration reference

| Env var                          | Default            | Effect                                                       |
|----------------------------------|--------------------|--------------------------------------------------------------|
| `PROMETHEUS_URL`                 | `http://localhost:9090` | Empty string disables the learner entirely.            |
| `BASELINE_SCRAPE_INTERVAL_S`     | `60`               | Time between scrapes. Clamped to ≥ 5.                        |
| `BASELINE_FIT_INTERVAL_S`        | `600`              | Time between fits. Clamped to ≥ 60.                          |
| `BASELINE_EXTRA_QUERIES_JSON`    | unset              | JSON-encoded list of extra `Query` objects.                  |

## Expected confidence lift

The Phase 12 confidence benchmark plus the design assumption for
Phase 13: **+10–15 percentage points** on the median rule, weighted
toward OOM, memory-leak, traffic-surge, and node-pressure scenarios
where the baseline-vs-current comparison is the dominant signal. At
the time of writing, the live laptop install runs `median=0.81`
without baselines (Phase 12 baseline); the Phase 13 + Phase 14
+ Phase 15 stack is the path to ≥ 0.90.

## Implementation notes

* The store is a separate SQLite file (`data/baselines.db`) rather
  than reusing `calibration.db`. The two stores have very different
  lifetimes: calibration rows are tiny + sticky; samples churn at
  ~1 row per pod per 60 s. Keeping them apart lets operators tune
  retention independently and back them up at different cadences.
* The graph node is wrapped in the same `pipeline_trace.instrumented`
  decorator as every other node — the SPA's pipeline timeline shows
  `enrich_with_baselines` as its own phase chip + the audit chain
  records every snapshot it returned.
* The diagnose prompt is appended to (not rewritten). Phase 12
  scenarios continue to pass — old incidents that don't have
  baselines just see an empty `baselines: []` field that renders to
  the empty string in `_render_baselines_block`.
