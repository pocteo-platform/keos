"""Background learner — polls Prometheus, persists samples, refits baselines.

Runs as an asyncio task started by `agent.serve`. Two cadences:

* **scrape tick (default 60 s)** — issue PromQL `/api/v1/query` for
  each configured query, parse the instant vector, and persist one
  `Sample` row per series. Wrapped in a wide try/except so a
  Prometheus blip never crashes the agent; the next tick retries.

* **fit tick (default 600 s)** — recompute every known baseline from
  the underlying samples. Prune samples older than the retention
  window. Update Prometheus metrics so the doctor and the SPA can
  report "n_keys / n_samples / n_anomalous" at a glance.

The query catalog is intentionally short. Seven series cover the
incidents the agent diagnoses today; the operator can extend the
catalog via `BASELINE_EXTRA_QUERIES_JSON` without re-deploying.

Failure modes are all soft: an unreachable Prometheus, an unparseable
response, an empty result vector — each is logged once at WARNING
and the learner keeps going. The graph node falls back to "no
baselines" when the store is empty, so the loop degrades cleanly
when Prometheus is missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from agent.baselines.k8s_proxy import K8sProxyClient, ProxyTarget
from agent.baselines.store import (
    BASELINE_FIT_INTERVAL_S,
    BASELINE_RETAIN_DAYS,
    BaselineStore,
    MetricKey,
    Sample,
    get_default_store,
)

log = logging.getLogger(__name__)


# Scrape cadence. Configurable so the doctor / tests can run a faster
# tick without recompiling. 60 s matches Prometheus's typical default
# and gives the EWMA enough movement per hour to be useful.
DEFAULT_SCRAPE_INTERVAL_S = 60

# How many days of samples to keep before pruning. Mirrors the
# constant in `store.py` so the learner and the store agree.
_RETAIN_SECONDS = BASELINE_RETAIN_DAYS * 86400


@dataclass(frozen=True)
class Query:
    """One PromQL series we sample.

    `metric` is the LOGICAL name we store under (e.g.
    `container_memory_rss_bytes`). `promql` is the query string sent
    to Prometheus. `namespace_label` and `scope_label` name the
    labels on the returned series that map to the store's
    `MetricKey.namespace` / `MetricKey.scope` fields — most pod-level
    series use `namespace` + `pod`; node-level use `""` + `node`.
    """

    metric: str
    promql: str
    namespace_label: str = "namespace"
    scope_label: str = "pod"


# Query catalogs by profile. `k8s` (default) targets a Prometheus
# scraping kubelet + kube-state-metrics inside a cluster — the
# production shape. `docker` targets a Prometheus scraping cAdvisor
# against the Docker socket — the laptop-demo shape. Switch via
# `BASELINE_QUERY_PROFILE`. Each profile feeds the same store +
# snapshot reader; the only difference is the label that names the
# scope (pod vs container).

# `docker` profile — cAdvisor-shaped series, scope = container name.
DOCKER_QUERIES: tuple[Query, ...] = (
    Query(
        metric="container_memory_rss_bytes",
        promql='sum by (name) (container_memory_rss{name!=""})',
        namespace_label="",
        scope_label="name",
    ),
    Query(
        metric="container_cpu_rate_5m",
        promql=('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[5m]))'),
        namespace_label="",
        scope_label="name",
    ),
    Query(
        metric="container_memory_working_set_bytes",
        promql='sum by (name) (container_memory_working_set_bytes{name!=""})',
        namespace_label="",
        scope_label="name",
    ),
    Query(
        metric="container_network_rx_rate_5m",
        promql='sum by (name) (rate(container_network_receive_bytes_total{name!=""}[5m]))',
        namespace_label="",
        scope_label="name",
    ),
)


# `k8s` profile — kubelet/cAdvisor + kube-state-metrics shape.
# Each query returns one series per pod (or node). This is the
# production catalog; covers the seven runbooks shipped today.
KUBELET_QUERIES: tuple[Query, ...] = (
    # Container memory + cpu — primary signals for OOM / hot pods.
    Query(
        metric="container_memory_rss_bytes",
        promql=('sum by (namespace, pod) (container_memory_rss{container!="POD",container!=""})'),
    ),
    Query(
        metric="container_cpu_rate_5m",
        promql=(
            "sum by (namespace, pod) ("
            'rate(container_cpu_usage_seconds_total{container!="POD",container!=""}[5m])'
            ")"
        ),
    ),
    # Restart counter — a series with a sudden bump is the
    # diagnostic gold standard for CrashLoopBackOff incidents.
    Query(
        metric="kube_pod_restarts_total",
        promql=("sum by (namespace, pod) (kube_pod_container_status_restarts_total)"),
    ),
    # Node-level series — keep namespace empty, scope = node.
    Query(
        metric="node_load1",
        promql="node_load1",
        namespace_label="",
        scope_label="instance",
    ),
    Query(
        metric="node_memory_available_bytes",
        promql="node_memory_MemAvailable_bytes",
        namespace_label="",
        scope_label="instance",
    ),
    Query(
        metric="node_fs_avail_bytes",
        promql=('min by (instance) (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay"})'),
        namespace_label="",
        scope_label="instance",
    ),
)


# Back-compat alias — earlier Phase 13 code (and tests) imported this
# name. Resolves to the k8s profile, matching the previous default.
DEFAULT_QUERIES: tuple[Query, ...] = KUBELET_QUERIES


def _queries_for_profile(profile: str) -> tuple[Query, ...]:
    """Resolve `BASELINE_QUERY_PROFILE` to a catalog. Unknown values
    fall back to `k8s` with a WARNING so a typo doesn't silently
    disable the learner."""
    p = (profile or "").strip().lower()
    if p == "docker":
        return DOCKER_QUERIES
    if p in ("", "k8s", "kubelet"):
        return KUBELET_QUERIES
    log.warning("BASELINE_QUERY_PROFILE=%r unknown; falling back to k8s profile", profile)
    return KUBELET_QUERIES


def _queries_from_env() -> tuple[Query, ...]:
    """Override / extend the default catalog at deploy time.

    `BASELINE_EXTRA_QUERIES_JSON` is parsed as a JSON array of
    `{metric, promql, namespace_label?, scope_label?}` objects.
    Invalid JSON falls back to the defaults with a WARNING.
    """
    profile = os.environ.get("BASELINE_QUERY_PROFILE", "k8s")
    base = _queries_for_profile(profile)
    raw = os.environ.get("BASELINE_EXTRA_QUERIES_JSON", "").strip()
    if not raw:
        return base
    try:
        items = json.loads(raw)
        extras = tuple(
            Query(
                metric=str(it["metric"]),
                promql=str(it["promql"]),
                namespace_label=str(it.get("namespace_label", "namespace")),
                scope_label=str(it.get("scope_label", "pod")),
            )
            for it in items
        )
        log.info("baselines: %d extra queries loaded from env", len(extras))
        return base + extras
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("BASELINE_EXTRA_QUERIES_JSON invalid (%s); using profile defaults", exc)
        return base


class Learner:
    """Polling + fitting loop. Public surface is small: `start`, `stop`,
    and `tick_once`. The latter is what tests drive."""

    def __init__(
        self,
        *,
        prometheus_url: str,
        store: BaselineStore | None = None,
        scrape_interval_s: int = DEFAULT_SCRAPE_INTERVAL_S,
        fit_interval_s: int = BASELINE_FIT_INTERVAL_S,
        queries: tuple[Query, ...] | None = None,
        http_timeout_s: float = 10.0,
    ) -> None:
        self.prometheus_url = prometheus_url.rstrip("/")
        self.store = store or get_default_store()
        self.scrape_interval_s = max(5, int(scrape_interval_s))
        self.fit_interval_s = max(60, int(fit_interval_s))
        self.queries = queries or _queries_from_env()
        self.http_timeout_s = float(http_timeout_s)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_fit_at: float = 0.0
        # k8s-proxy mode — `PROMETHEUS_URL=k8s-proxy://<ns>/<svc>[:<port>]`.
        # When set, every query goes through the kubernetes apiserver
        # proxy instead of httpx.AsyncClient — used in Phase 13c where
        # Prometheus runs inside the k3d cluster and isn't reachable
        # directly from the compose-side agent.
        self._proxy_client: K8sProxyClient | None = None
        if self.prometheus_url.startswith("k8s-proxy://"):
            self._proxy_client = K8sProxyClient(ProxyTarget.from_url(self.prometheus_url))

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="baseline-learner")
        log.info(
            "baselines: learner started (scrape=%ds fit=%ds queries=%d prometheus=%s)",
            self.scrape_interval_s,
            self.fit_interval_s,
            len(self.queries),
            self.prometheus_url,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        # Run one scrape immediately so the doctor reports samples
        # within seconds of `make up`, not after the first 60 s tick.
        await self.tick_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.scrape_interval_s)
            except TimeoutError:
                pass
            if self._stop.is_set():
                return
            await self.tick_once()

    async def tick_once(self) -> int:
        """One full scrape + (maybe) fit cycle. Returns the number of
        samples inserted on this tick; useful in tests."""
        try:
            samples = await self._scrape_all()
        except Exception as exc:  # noqa: BLE001 — learner must never crash the agent
            log.warning("baselines: scrape failed (%s); will retry next tick", exc)
            return 0
        inserted = self.store.insert_samples(samples)
        if inserted:
            log.debug("baselines: inserted %d samples this tick", inserted)
            # Per-metric counter + total-keys gauge for dashboards.
            try:
                from agent.metrics import baseline_keys_known, baseline_samples_total

                per_metric: dict[str, int] = {}
                for s in samples:
                    per_metric[s.key.metric] = per_metric.get(s.key.metric, 0) + 1
                for metric, n in per_metric.items():
                    baseline_samples_total.labels(metric=metric).inc(n)
                baseline_keys_known.set(len(self.store.list_keys()))
            except Exception as exc:  # noqa: BLE001
                log.debug("baseline metric emit failed (%s); continuing", exc)
        now = time.time()
        if now - self._last_fit_at >= self.fit_interval_s:
            try:
                self._refit_all()
                self.store.prune_older_than(_RETAIN_SECONDS)
            except Exception as exc:  # noqa: BLE001
                log.warning("baselines: refit/prune failed (%s); continuing", exc)
            self._last_fit_at = now
        return inserted

    # ---- internals ---------------------------------------------------------

    async def _scrape_all(self) -> list[Sample]:
        if self._proxy_client is not None:
            return await self._scrape_via_proxy()
        return await self._scrape_via_httpx()

    async def _scrape_via_httpx(self) -> list[Sample]:
        out: list[Sample] = []
        async with httpx.AsyncClient(timeout=self.http_timeout_s) as client:
            for q in self.queries:
                series = await self._query_http(client, q)
                out.extend(series)
        return out

    async def _scrape_via_proxy(self) -> list[Sample]:
        """Same shape as `_scrape_via_httpx` but every PromQL goes
        through the Kubernetes apiserver proxy. Each call is wrapped
        in its own try/except so one slow / failing query doesn't
        starve the others.

        Suppression: if every query in a tick fails with the same
        underlying cause (typically: Prometheus isn't installed →
        `services "prometheus" not found`), log it once at INFO and
        switch to silent-skip mode. The flag resets on agent restart;
        an operator who installs Prometheus afterwards just bounces
        the agent pod.
        """
        assert self._proxy_client is not None
        if getattr(self, "_proxy_disabled", False):
            return []
        out: list[Sample] = []
        per_query_errors: list[str] = []
        for q in self.queries:
            try:
                body = await self._proxy_client.query_async(q.promql)
            except Exception as exc:  # noqa: BLE001 — soft-fail per query
                # Compact error string — strip the full HTTP response body
                # that the k8s client tacks on; keep just the reason.
                msg = str(exc).split("\n", 1)[0][:200]
                per_query_errors.append(msg)
                continue
            out.extend(parse_prom_response(q, body))
        if per_query_errors and len(per_query_errors) == len(self.queries):
            # Every query failed this tick. If the failure looks like
            # "Prometheus service not found" — disable for the rest of
            # the process lifetime; otherwise just log once at WARNING.
            sample = per_query_errors[0]
            if "prometheus" in sample.lower() and "not found" in sample.lower():
                log.info(
                    "baselines: Prometheus service not reachable via "
                    "apiserver proxy — disabling baseline learner for this "
                    "session. Install kube-prometheus-stack and restart the "
                    "agent to re-enable. (last error: %s)",
                    sample,
                )
                self._proxy_disabled = True
            else:
                log.warning(
                    "baselines: all %d queries failed this tick (first error: %s)",
                    len(per_query_errors),
                    sample,
                )
        elif per_query_errors:
            log.warning(
                "baselines: %d/%d queries failed this tick (first error: %s)",
                len(per_query_errors),
                len(self.queries),
                per_query_errors[0],
            )
        return out

    async def _query_http(self, client: httpx.AsyncClient, q: Query) -> list[Sample]:
        url = f"{self.prometheus_url}/api/v1/query"
        try:
            resp = await client.get(url, params={"query": q.promql})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("baselines: prometheus query %r failed (%s)", q.metric, exc)
            return []
        return parse_prom_response(q, resp.json())

    def _refit_all(self) -> None:
        """Recompute baselines for every known key from the underlying
        sample buffer. Idempotent — `upsert_fit` overwrites in place."""
        for key in self.store.list_keys():
            fit = self.store.fit_from_samples(key)
            if fit is not None:
                self.store.upsert_fit(fit)


def parse_prom_response(q: Query, body: dict) -> list[Sample]:
    """Pure helper: turn one Prometheus `/api/v1/query` response into
    `Sample` rows. Extracted from `Learner._query` so the parser can be
    unit-tested without spinning up an HTTP mock.

    Series that don't expose a usable `scope_label` are skipped rather
    than stored under an empty string — we can't disambiguate them at
    read time and the snapshot reader would mix unrelated pods.
    """
    if (body or {}).get("status") != "success":
        log.warning("baselines: prometheus returned non-success for %r", q.metric)
        return []
    result = (body.get("data") or {}).get("result") or []
    now = time.time()
    out: list[Sample] = []
    for series in result:
        metric_labels = series.get("metric") or {}
        value = series.get("value") or [None, None]
        try:
            ts = float(value[0]) if value[0] is not None else now
            v = float(value[1])
        except (TypeError, ValueError):
            continue
        ns = metric_labels.get(q.namespace_label, "") if q.namespace_label else ""
        scope = metric_labels.get(q.scope_label, "")
        if not scope:
            continue
        out.append(
            Sample(
                key=MetricKey(metric=q.metric, namespace=ns, scope=scope),
                ts=ts,
                value=v,
            )
        )
    return out
