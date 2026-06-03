"""Hermetic tests for the Phase 13 baselines package.

No live Prometheus, no live cluster. Just:

  * the pure math (`agent.baselines.math`),
  * the SQLite store (`agent.baselines.store`),
  * the snapshot reader (`agent.baselines.snapshot`),
  * the graph node's integration with both.

The learner has an HTTPX-driven path; we cover its `_query` shape by
mocking `httpx.AsyncClient.get` rather than spinning up a fake
Prometheus.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from agent.baselines.math import (
    BASELINE_MIN_SAMPLES,
    BASELINE_SEASONAL_MIN_SAMPLES,
    BASELINE_SEASONAL_PERIODS,
    ewma_mean_var,
    hour_of_day_medians,
    quantile,
    safe_zscore,
)
from agent.baselines.snapshot import (
    BaselineSnapshot,
    snapshot_for_pod,
    snapshot_one,
)
from agent.baselines.store import (
    BaselineFit,
    BaselineStore,
    MetricKey,
    Sample,
)

# ─── Math primitives ────────────────────────────────────────────────────


def test_ewma_empty_returns_zeros() -> None:
    stats = ewma_mean_var([])
    assert stats.n == 0
    assert stats.mean == 0.0
    assert stats.std == 0.0


def test_ewma_single_sample_returns_that_value() -> None:
    stats = ewma_mean_var([7.5])
    assert stats.n == 1
    assert stats.mean == pytest.approx(7.5)
    assert stats.std == 0.0


def test_ewma_constant_series_has_zero_variance() -> None:
    stats = ewma_mean_var([42.0] * 100)
    assert stats.mean == pytest.approx(42.0)
    assert stats.std == pytest.approx(0.0, abs=1e-9)


def test_ewma_tracks_mean_drift() -> None:
    # Stationary at 10 for a while, then step to 20. EWMA should be
    # closer to 20 than to 10 after enough new samples land.
    series = [10.0] * 200 + [20.0] * 200
    stats = ewma_mean_var(series, alpha=0.1)
    assert stats.mean > 15.0
    assert stats.std > 0.0


def test_safe_zscore_returns_zero_when_sigma_is_zero() -> None:
    # Avoids the "infinity σ above baseline" bug on flat series.
    assert safe_zscore(10.0, 9.999999, 0.0) == 0.0


def test_safe_zscore_positive_when_above_mean() -> None:
    # ~3 sigma above.
    z = safe_zscore(current=130.0, mean=100.0, std=10.0)
    assert z == pytest.approx(3.0)


def test_safe_zscore_negative_when_below_mean() -> None:
    z = safe_zscore(current=70.0, mean=100.0, std=10.0)
    assert z == pytest.approx(-3.0)


def test_hour_of_day_medians_returns_periods_buckets() -> None:
    medians = hour_of_day_medians([], periods=BASELINE_SEASONAL_PERIODS)
    assert len(medians) == BASELINE_SEASONAL_PERIODS
    assert all(m is None for m in medians)


def test_hour_of_day_medians_assigns_to_correct_bucket() -> None:
    # Pick a timestamp inside hour-bucket 5; assert it lands there.
    ts_in_h5 = 5 * 3600 + 600
    medians = hour_of_day_medians([(ts_in_h5, 42.0)])
    assert medians[5] == pytest.approx(42.0)
    # All other buckets are still empty.
    assert sum(1 for m in medians if m is not None) == 1


def test_quantile_matches_calibration_implementation() -> None:
    # Mirrors `agent.calibration.store._quantile` semantics.
    assert quantile([], 0.5) == 0.0
    assert quantile([0.7], 0.99) == 0.7
    assert quantile([0.0, 0.5, 1.0], 0.5) == pytest.approx(0.5)
    assert quantile([0.0, 0.5, 1.0], 0.25) == pytest.approx(0.25)


# ─── Store: CRUD + pruning + fit ────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> BaselineStore:
    return BaselineStore(tmp_path / "baselines.db")


def test_store_creates_db_with_schema(store: BaselineStore) -> None:
    assert store.count_samples() == 0
    assert store.list_keys() == []


def test_insert_samples_persists_rows(store: BaselineStore) -> None:
    key = MetricKey("container_memory_rss_bytes", "workloads", "nginx-1")
    samples = [Sample(key=key, ts=time.time() + i, value=100.0 + i) for i in range(10)]
    n = store.insert_samples(samples)
    assert n == 10
    assert store.count_samples() == 10
    assert store.list_keys() == [key]


def test_insert_empty_is_noop(store: BaselineStore) -> None:
    assert store.insert_samples([]) == 0
    assert store.count_samples() == 0


def test_recent_samples_returns_oldest_first(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    base_ts = 1_700_000_000.0
    store.insert_samples([Sample(key=key, ts=base_ts + i, value=float(i)) for i in range(20)])
    recent = store.recent_samples(key, limit=5)
    # Most-recent 5 in chronological order: ts 15..19.
    assert [s.value for s in recent] == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_fit_from_samples_returns_none_below_min_samples(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    store.insert_samples(
        [Sample(key=key, ts=time.time() + i, value=100.0) for i in range(BASELINE_MIN_SAMPLES - 1)]
    )
    assert store.fit_from_samples(key) is None


def test_fit_from_samples_returns_fit_above_min_samples(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    store.insert_samples(
        [Sample(key=key, ts=time.time() + i, value=100.0) for i in range(BASELINE_MIN_SAMPLES)]
    )
    fit = store.fit_from_samples(key)
    assert fit is not None
    assert fit.mean == pytest.approx(100.0)
    assert fit.std == pytest.approx(0.0, abs=1e-6)
    assert fit.n_samples == BASELINE_MIN_SAMPLES
    # Below the seasonal threshold, so all hour buckets are None.
    assert all(m is None for m in fit.hour_medians)


def test_fit_above_seasonal_threshold_populates_hour_medians(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    # Spread samples across many hours.
    base_ts = 1_700_000_000.0
    samples = [
        Sample(key=key, ts=base_ts + i * 60, value=10.0 + (i % 24))
        for i in range(BASELINE_SEASONAL_MIN_SAMPLES + 50)
    ]
    store.insert_samples(samples)
    fit = store.fit_from_samples(key)
    assert fit is not None
    assert any(m is not None for m in fit.hour_medians)


def test_upsert_fit_round_trips(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    fit = BaselineFit(
        key=key,
        mean=42.0,
        std=2.5,
        n_samples=100,
        hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
        computed_at=time.time(),
    )
    store.upsert_fit(fit)
    loaded = store.get_fit(key)
    assert loaded is not None
    assert loaded.mean == pytest.approx(42.0)
    assert loaded.std == pytest.approx(2.5)


def test_prune_older_than_drops_old_rows(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    now = time.time()
    rows = [
        Sample(key=key, ts=now - 30 * 86400, value=1.0),  # 30 days old
        Sample(key=key, ts=now - 1 * 86400, value=2.0),  # 1 day old
        Sample(key=key, ts=now, value=3.0),  # fresh
    ]
    store.insert_samples(rows)
    dropped = store.prune_older_than(7 * 86400)  # keep last 7 days
    assert dropped == 1
    assert store.count_samples() == 2


def test_list_keys_filters_by_scope(store: BaselineStore) -> None:
    keys = [
        MetricKey("m", "a", "pod-1"),
        MetricKey("m", "a", "pod-2"),
        MetricKey("m", "b", "pod-3"),
    ]
    for k in keys:
        store.insert_samples([Sample(key=k, ts=time.time(), value=1.0)])
    assert store.list_keys(namespace="a") == [
        MetricKey("m", "a", "pod-1"),
        MetricKey("m", "a", "pod-2"),
    ]
    assert store.list_keys(namespace="a", scope="pod-1") == [MetricKey("m", "a", "pod-1")]


def test_reset_wipes_everything(store: BaselineStore) -> None:
    key = MetricKey("m", "ns", "pod")
    store.insert_samples([Sample(key=key, ts=time.time(), value=1.0)])
    store.upsert_fit(
        BaselineFit(
            key=key,
            mean=1.0,
            std=0.1,
            n_samples=1,
            hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
            computed_at=time.time(),
        )
    )
    store.reset()
    assert store.count_samples() == 0
    assert store.get_fit(key) is None


# ─── Snapshot: pure function ────────────────────────────────────────────


def test_snapshot_missing_when_no_current_and_no_fit() -> None:
    key = MetricKey("m", "ns", "pod")
    snap = snapshot_one(key, current=None, fit=None)
    assert snap.status == "missing"
    assert snap.current is None


def test_snapshot_learning_when_current_present_but_no_fit() -> None:
    key = MetricKey("m", "ns", "pod")
    snap = snapshot_one(key, current=120.0, fit=None)
    assert snap.status == "learning"
    assert snap.current == 120.0
    assert snap.sigma_above is None


def test_snapshot_ok_computes_sigma_above() -> None:
    key = MetricKey("m", "ns", "pod")
    fit = BaselineFit(
        key=key,
        mean=100.0,
        std=10.0,
        n_samples=500,
        hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
        computed_at=time.time(),
    )
    snap = snapshot_one(key, current=130.0, fit=fit)
    assert snap.status == "ok"
    assert snap.sigma_above == pytest.approx(3.0)
    assert snap.seasonal_residual is None  # no hour medians populated


def test_snapshot_ok_computes_seasonal_residual_when_hour_median_present() -> None:
    key = MetricKey("m", "ns", "pod")
    # Bucket idx 3 carries a value of 90.
    medians: list[float | None] = [None] * BASELINE_SEASONAL_PERIODS
    medians[3] = 90.0
    fit = BaselineFit(
        key=key,
        mean=100.0,
        std=10.0,
        n_samples=BASELINE_SEASONAL_MIN_SAMPLES + 1,
        hour_medians=medians,
        computed_at=time.time(),
    )
    # Force `now` to fall in hour-bucket 3.
    fake_now = 3 * 3600 + 60
    snap = snapshot_one(key, current=130.0, fit=fit, now=fake_now)
    assert snap.seasonal_residual == pytest.approx(40.0)  # 130 − 90


def test_snapshot_is_anomalous_flags_high_sigma() -> None:
    snap = BaselineSnapshot(
        metric="m",
        namespace="n",
        scope="s",
        status="ok",
        current=200.0,
        mean=100.0,
        std=10.0,
        sigma_above=10.0,
    )
    assert snap.is_anomalous()


def test_snapshot_is_not_anomalous_when_status_is_learning() -> None:
    snap = BaselineSnapshot(
        metric="m",
        namespace="n",
        scope="s",
        status="learning",
        current=999.0,
        sigma_above=99.0,  # ignored because status != ok
    )
    assert not snap.is_anomalous()


# ─── Snapshot: store integration ────────────────────────────────────────


def test_snapshot_for_pod_empty_returns_empty_list(store: BaselineStore) -> None:
    assert snapshot_for_pod("workloads", "ghost", store=store) == []


def test_snapshot_for_pod_returns_only_pod_keys(store: BaselineStore) -> None:
    keys = [
        MetricKey("mem", "workloads", "alpha"),
        MetricKey("cpu", "workloads", "alpha"),
        MetricKey("mem", "workloads", "beta"),  # should be excluded
    ]
    for key in keys:
        # Enough samples to fit so status=ok.
        store.insert_samples(
            [Sample(key=key, ts=time.time() + i, value=10.0) for i in range(BASELINE_MIN_SAMPLES)]
        )
        store.upsert_fit(
            BaselineFit(
                key=key,
                mean=10.0,
                std=1.0,
                n_samples=BASELINE_MIN_SAMPLES,
                hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
                computed_at=time.time(),
            )
        )
    snaps = snapshot_for_pod("workloads", "alpha", store=store)
    assert {s.metric for s in snaps} == {"mem", "cpu"}


def test_snapshot_for_pod_ranks_anomalous_first(store: BaselineStore) -> None:
    keys = [
        MetricKey("normal", "n", "pod"),
        MetricKey("hot", "n", "pod"),
    ]
    for key in keys:
        store.insert_samples(
            [Sample(key=key, ts=time.time() + i, value=10.0) for i in range(BASELINE_MIN_SAMPLES)]
        )
    # `hot` will be way above mean; `normal` is at baseline.
    store.upsert_fit(
        BaselineFit(
            key=keys[0],
            mean=10.0,
            std=1.0,
            n_samples=200,
            hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
            computed_at=time.time(),
        )
    )
    store.upsert_fit(
        BaselineFit(
            key=keys[1],
            mean=1.0,
            std=0.1,
            n_samples=200,
            hour_medians=[None] * BASELINE_SEASONAL_PERIODS,
            computed_at=time.time(),
        )
    )
    snaps = snapshot_for_pod("n", "pod", store=store)
    assert snaps[0].metric == "hot"


# ─── Learner — minimal mock-based coverage of the scrape path ───────────


@pytest.mark.asyncio
async def test_learner_tick_persists_samples_from_scrape(store: BaselineStore) -> None:
    """Drive `tick_once` by replacing `_scrape_all` with a canned
    return value. Avoids the httpx async-context-manager mocking
    pitfalls and isolates the learner's persist + metric path."""
    from agent.baselines.learner import Learner

    learner = Learner(prometheus_url="http://prom:9090", store=store)
    canned = [
        Sample(
            key=MetricKey("container_memory_rss_bytes", "workloads", "nginx-demo-1"),
            ts=time.time(),
            value=12345.6,
        ),
        Sample(
            key=MetricKey("container_memory_rss_bytes", "workloads", "nginx-demo-2"),
            ts=time.time(),
            value=23456.7,
        ),
    ]

    async def _stub() -> list[Sample]:
        return canned

    learner._scrape_all = _stub  # type: ignore[assignment]
    inserted = await learner.tick_once()
    assert inserted == 2
    keys = store.list_keys()
    assert MetricKey("container_memory_rss_bytes", "workloads", "nginx-demo-1") in keys
    assert MetricKey("container_memory_rss_bytes", "workloads", "nginx-demo-2") in keys


@pytest.mark.asyncio
async def test_learner_swallows_scrape_errors(store: BaselineStore) -> None:
    """A scrape that raises must NOT crash the loop; tick returns 0."""
    from agent.baselines.learner import Learner

    learner = Learner(prometheus_url="http://prom:9090", store=store)

    async def _boom() -> list[Sample]:
        raise RuntimeError("simulated prometheus down")

    learner._scrape_all = _boom  # type: ignore[assignment]
    assert await learner.tick_once() == 0


def test_parse_prom_response_emits_one_sample_per_series() -> None:
    """The PromQL → Sample translation is the only learner code that
    cares about Prometheus's shape. Hit it directly."""
    from agent.baselines.learner import Query, parse_prom_response

    body = {
        "status": "success",
        "data": {
            "result": [
                {
                    "metric": {"namespace": "workloads", "pod": "nginx-demo-1"},
                    "value": [1_700_000_000, "12345.6"],
                },
                {
                    "metric": {"namespace": "workloads", "pod": "nginx-demo-2"},
                    "value": [1_700_000_000, "23456.7"],
                },
            ]
        },
    }
    samples = parse_prom_response(
        Query(metric="container_memory_rss_bytes", promql="ignored"), body
    )
    assert [s.key.scope for s in samples] == ["nginx-demo-1", "nginx-demo-2"]
    assert samples[0].value == pytest.approx(12345.6)
    assert samples[0].ts == pytest.approx(1_700_000_000.0)


def test_parse_prom_response_skips_series_without_scope_label() -> None:
    from agent.baselines.learner import Query, parse_prom_response

    body = {
        "status": "success",
        "data": {
            "result": [
                {"metric": {"namespace": "workloads"}, "value": [0, "1.0"]},
            ]
        },
    }
    assert parse_prom_response(Query(metric="m", promql="x"), body) == []


def test_parse_prom_response_returns_empty_on_non_success() -> None:
    from agent.baselines.learner import Query, parse_prom_response

    assert parse_prom_response(Query(metric="m", promql="x"), {"status": "error"}) == []


# ─── Query-profile selection ───────────────────────────────────────────


def test_queries_for_profile_k8s_returns_kubelet_catalog() -> None:
    from agent.baselines.learner import KUBELET_QUERIES, _queries_for_profile

    assert _queries_for_profile("k8s") == KUBELET_QUERIES
    assert _queries_for_profile("kubelet") == KUBELET_QUERIES
    # Empty string maps to default k8s profile.
    assert _queries_for_profile("") == KUBELET_QUERIES


def test_queries_for_profile_docker_returns_cadvisor_catalog() -> None:
    from agent.baselines.learner import DOCKER_QUERIES, _queries_for_profile

    assert _queries_for_profile("docker") == DOCKER_QUERIES
    # Case-insensitive.
    assert _queries_for_profile("DOCKER") == DOCKER_QUERIES


def test_queries_for_profile_unknown_falls_back_to_k8s() -> None:
    from agent.baselines.learner import KUBELET_QUERIES, _queries_for_profile

    # Typo must NOT silently disable the learner — fall back to k8s
    # so production deployments keep collecting samples.
    assert _queries_for_profile("k8z") == KUBELET_QUERIES


def test_learner_class_exposes_refit_method() -> None:
    """Regression guard: `_refit_all` MUST be a method on Learner, not
    an orphaned module-level function. The fit branch is triggered on
    every tick where `now - last_fit_at >= fit_interval_s` — if it's
    missing, the wide try/except swallows the AttributeError silently
    and baselines never get fitted, but the bug isn't visible in tests
    that don't actually call refit."""
    from agent.baselines.learner import Learner

    assert hasattr(Learner, "_refit_all"), (
        "_refit_all must be a Learner method; otherwise tick_once's "
        "fit branch silently fails on every cycle"
    )


def test_docker_profile_targets_name_label() -> None:
    """Sanity check that the docker profile sets `scope_label="name"`
    so the learner extracts the container name from cAdvisor's labels."""
    from agent.baselines.learner import DOCKER_QUERIES

    for q in DOCKER_QUERIES:
        assert q.scope_label == "name", f"{q.metric} should target the `name` label"
        assert q.namespace_label == ""


# ─── Graph node integration ─────────────────────────────────────────────


def test_graph_node_returns_empty_snapshots_on_unknown_pod(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_audit: Path,  # noqa: ARG001 — redirects audit.emit to tmp_path
) -> None:
    """`node_enrich_with_baselines` must NOT crash when no Prometheus
    data exists for the target pod. It returns `baselines=[]` and the
    graph keeps going. Mirrors the k8sgpt_analyze fail-OPEN-but-empty
    pattern."""
    from agent.graph import node_enrich_with_baselines

    # Point the snapshot reader at a fresh tmp DB so the test is isolated.
    fresh = BaselineStore(tmp_path / "bl.db")
    monkeypatch.setattr("agent.baselines.snapshot.get_default_store", lambda: fresh)
    state: dict[str, Any] = {
        "incident_id": "test-incident",
        "namespace": "workloads",
        "pod": "ghost-pod",
    }
    out = node_enrich_with_baselines(state)  # type: ignore[arg-type]
    assert out == {"baselines": []}


def test_graph_node_skips_when_namespace_or_pod_missing(
    tmp_audit: Path,  # noqa: ARG001 — redirects audit.emit to tmp_path
) -> None:
    """No ns/pod = no enrich. The graph still inserts the audit row
    (with snapshot_count=0) but `baselines` stays empty."""
    from agent.graph import node_enrich_with_baselines

    state: dict[str, Any] = {"incident_id": "no-target"}
    out = node_enrich_with_baselines(state)  # type: ignore[arg-type]
    assert out == {"baselines": []}


# ─── Diagnose prompt assembly ───────────────────────────────────────────


def test_render_baselines_block_empty_returns_empty_string() -> None:
    from agent.llm import _render_baselines_block

    assert _render_baselines_block(None) == ""
    assert _render_baselines_block([]) == ""


def test_render_baselines_block_formats_ok_snapshot() -> None:
    from agent.llm import _render_baselines_block

    block = _render_baselines_block(
        [
            {
                "metric": "container_memory_rss_bytes",
                "status": "ok",
                "current": 838 * 1024 * 1024,
                "mean": 412 * 1024 * 1024,
                "std": 18 * 1024 * 1024,
                "sigma_above": 23.6,
                "seasonal_residual": 426 * 1024 * 1024,
                "n_samples": 942,
            }
        ]
    )
    assert "METRIC BASELINES" in block
    assert "container_memory_rss_bytes" in block
    assert "MiB" in block  # human-formatted
    assert "+23.6σ" in block
    assert "n=942" in block


def test_render_baselines_block_formats_learning_snapshot() -> None:
    from agent.llm import _render_baselines_block

    block = _render_baselines_block(
        [
            {
                "metric": "container_cpu_rate_5m",
                "status": "learning",
                "current": 0.25,
            }
        ]
    )
    assert "still learning" in block
    assert "container_cpu_rate_5m" in block
