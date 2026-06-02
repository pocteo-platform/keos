"""SQLite-backed sample buffer + fitted-baseline cache.

Two tables, both keyed on a `MetricKey` triple `(metric, namespace,
pod_or_node)`:

* `samples` — append-only ring buffer of `(metric_key, ts, value)`.
  Pruned past `BASELINE_RETAIN_DAYS` by a periodic call from the
  learner. Indexed on `(metric_key, ts DESC)` so the most-recent
  window read by the snapshot API is a single index range scan.

* `baselines` — one row per `metric_key` with the fitted statistics
  (mean, std, hour-of-day medians, sample count, fit timestamp). The
  snapshot reader returns these directly; the learner rewrites them
  every `BASELINE_FIT_INTERVAL_S` seconds from the underlying
  samples.

Concurrency: WAL mode + a threading.Lock around writes — same pattern
as `agent.calibration.store.CalibrationStore`. Reads are lock-free.

We deliberately use a separate database file (`baselines.db`) rather
than reusing `calibration.db`. The two stores serve different
lifetimes (calibration rows are tiny + sticky; samples churn) and
keeping them apart lets operators tune retention independently and
back them up at different cadences.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.baselines.math import (
    BASELINE_MIN_SAMPLES,
    BASELINE_SEASONAL_MIN_SAMPLES,
    BASELINE_SEASONAL_PERIODS,
    ewma_mean_var,
    hour_of_day_medians,
)
from agent.config import settings

log = logging.getLogger(__name__)


# How long to keep raw samples before the learner's prune sweep
# evicts them. 14 days = 2 × the seasonal window, so a rolling fit
# always has fresh data on both sides of the period boundary.
BASELINE_RETAIN_DAYS = 14

# How often the learner refits baselines from the sample buffer. 10 min
# is a good balance: fast enough that a new pod becomes "known" within
# minutes of arrival, slow enough that fitting cost is amortised.
BASELINE_FIT_INTERVAL_S = 600

# Default cap on the rolling-fit window. ~2 × seasonal threshold so
# variance stays sensitive to the last week's behaviour without
# blowing memory on long-running edge installs.
BASELINE_FIT_WINDOW = 2 * BASELINE_SEASONAL_MIN_SAMPLES


# ----- Schema --------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    metric        TEXT NOT NULL,
    namespace     TEXT NOT NULL,
    scope         TEXT NOT NULL,     -- pod name or node name
    ts            REAL NOT NULL,     -- unix seconds (UTC)
    value         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_key_ts
    ON samples (metric, namespace, scope, ts DESC);

CREATE TABLE IF NOT EXISTS baselines (
    metric            TEXT NOT NULL,
    namespace         TEXT NOT NULL,
    scope             TEXT NOT NULL,
    mean              REAL NOT NULL,
    std               REAL NOT NULL,
    n_samples         INTEGER NOT NULL,
    hour_medians_json TEXT NOT NULL,    -- JSON array of length 24
    computed_at       REAL NOT NULL,    -- unix seconds (UTC)
    PRIMARY KEY (metric, namespace, scope)
);
"""


# ----- Data types ----------------------------------------------------------


@dataclass(frozen=True)
class MetricKey:
    """Triple that identifies a baseline series.

    `scope` is the pod name for pod-level metrics or the node name for
    node-level metrics. Empty `namespace` is fine for node-scoped
    series (`node_load1`, `node_filesystem_avail_bytes`, …).
    """

    metric: str
    namespace: str
    scope: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.metric, self.namespace, self.scope)


@dataclass(frozen=True)
class Sample:
    key: MetricKey
    ts: float
    value: float


@dataclass
class BaselineFit:
    """Persisted, queryable form of a fitted baseline."""

    key: MetricKey
    mean: float
    std: float
    n_samples: int
    # 24 entries, one per hour-of-day bucket. `None` means no samples
    # in that bucket yet.
    hour_medians: list[float | None] = field(default_factory=list)
    computed_at: float = 0.0


# ----- Store ---------------------------------------------------------------


class BaselineStore:
    """Thread-safe SQLite wrapper. One DB file; per-call connections."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as cx:
            cx.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        cx = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA journal_mode = WAL;")
        cx.execute("PRAGMA synchronous = NORMAL;")
        return cx

    # ---- Write side --------------------------------------------------------

    def insert_samples(self, samples: list[Sample]) -> int:
        """Bulk insert. Returns the number of rows actually persisted.

        Called by the learner after each Prometheus poll. Empty input
        is a no-op (no transaction overhead)."""
        if not samples:
            return 0
        rows = [
            (s.key.metric, s.key.namespace, s.key.scope, float(s.ts), float(s.value))
            for s in samples
        ]
        with self._lock, self._connect() as cx:
            cx.executemany(
                "INSERT INTO samples (metric, namespace, scope, ts, value) VALUES (?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def upsert_fit(self, fit: BaselineFit) -> None:
        """Persist (overwrite) a fitted baseline for one MetricKey."""
        with self._lock, self._connect() as cx:
            cx.execute(
                """
                INSERT INTO baselines
                    (metric, namespace, scope, mean, std, n_samples, hour_medians_json, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric, namespace, scope) DO UPDATE SET
                    mean              = excluded.mean,
                    std               = excluded.std,
                    n_samples         = excluded.n_samples,
                    hour_medians_json = excluded.hour_medians_json,
                    computed_at       = excluded.computed_at
                """,
                (
                    fit.key.metric,
                    fit.key.namespace,
                    fit.key.scope,
                    float(fit.mean),
                    float(fit.std),
                    int(fit.n_samples),
                    json.dumps(fit.hour_medians),
                    float(fit.computed_at),
                ),
            )

    def prune_older_than(self, max_age_seconds: float) -> int:
        """Drop samples + baselines older than the cutoff. Returns the
        number of sample rows removed (baselines are upserted in place
        so they don't accumulate)."""
        cutoff = time.time() - float(max_age_seconds)
        with self._lock, self._connect() as cx:
            cur = cx.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            return int(cur.rowcount or 0)

    def reset(self) -> None:
        """Wipe everything. Used by tests + the doctor's reset recipe."""
        with self._lock, self._connect() as cx:
            cx.execute("DELETE FROM samples")
            cx.execute("DELETE FROM baselines")

    # ---- Read side ---------------------------------------------------------

    def recent_samples(self, key: MetricKey, limit: int = BASELINE_FIT_WINDOW) -> list[Sample]:
        """Return the most-recent `limit` samples for one key, oldest-first.

        Oldest-first makes EWMA convergence behave correctly (recent
        samples get weighted more by virtue of being processed last).
        """
        with self._connect() as cx:
            rows = cx.execute(
                """
                SELECT ts, value FROM samples
                WHERE metric = ? AND namespace = ? AND scope = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (key.metric, key.namespace, key.scope, int(limit)),
            ).fetchall()
        return [Sample(key=key, ts=float(r["ts"]), value=float(r["value"])) for r in reversed(rows)]

    def list_keys(self, namespace: str | None = None, scope: str | None = None) -> list[MetricKey]:
        """All known keys, optionally filtered by ns / scope. Used by
        the snapshot reader to pull every metric for a given pod."""
        clauses, params = [], []
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as cx:
            rows = cx.execute(
                f"""
                SELECT DISTINCT metric, namespace, scope
                FROM samples{where}
                ORDER BY metric, namespace, scope
                """,
                params,
            ).fetchall()
        return [MetricKey(r["metric"], r["namespace"], r["scope"]) for r in rows]

    def get_fit(self, key: MetricKey) -> BaselineFit | None:
        with self._connect() as cx:
            row = cx.execute(
                """
                SELECT mean, std, n_samples, hour_medians_json, computed_at
                FROM baselines
                WHERE metric = ? AND namespace = ? AND scope = ?
                """,
                (key.metric, key.namespace, key.scope),
            ).fetchone()
        if row is None:
            return None
        return BaselineFit(
            key=key,
            mean=float(row["mean"]),
            std=float(row["std"]),
            n_samples=int(row["n_samples"]),
            hour_medians=json.loads(row["hour_medians_json"]),
            computed_at=float(row["computed_at"]),
        )

    def fit_from_samples(self, key: MetricKey) -> BaselineFit | None:
        """Recompute a baseline from the stored samples for one key.

        Returns None when there are fewer than `BASELINE_MIN_SAMPLES`
        samples — the snapshot reader treats that as "still learning".

        Called by the learner on its fit tick; tests call it directly.
        """
        samples = self.recent_samples(key)
        if len(samples) < BASELINE_MIN_SAMPLES:
            return None
        stats = ewma_mean_var(s.value for s in samples)
        if len(samples) >= BASELINE_SEASONAL_MIN_SAMPLES:
            medians = hour_of_day_medians(
                ((s.ts, s.value) for s in samples),
                periods=BASELINE_SEASONAL_PERIODS,
            )
        else:
            medians = [None] * BASELINE_SEASONAL_PERIODS
        return BaselineFit(
            key=key,
            mean=stats.mean,
            std=stats.std,
            n_samples=stats.n,
            hour_medians=medians,
            computed_at=time.time(),
        )

    def count_samples(self, key: MetricKey | None = None) -> int:
        """Diagnostic helper; used by the doctor + tests."""
        with self._connect() as cx:
            if key is None:
                row = cx.execute("SELECT COUNT(*) AS n FROM samples").fetchone()
            else:
                row = cx.execute(
                    """
                    SELECT COUNT(*) AS n FROM samples
                    WHERE metric = ? AND namespace = ? AND scope = ?
                    """,
                    (key.metric, key.namespace, key.scope),
                ).fetchone()
        return int(row["n"]) if row else 0


# ----- Default instance ----------------------------------------------------

_default: BaselineStore | None = None


def _default_db_path() -> Path:
    """`data/baselines.db` next to the audit log + calibration DB."""
    return Path(settings.audit_log_path).parent / "baselines.db"


def get_default_store() -> BaselineStore:
    """Singleton accessor used by the graph node + the FastAPI handler."""
    global _default
    if _default is None:
        _default = BaselineStore(_default_db_path())
    return _default
