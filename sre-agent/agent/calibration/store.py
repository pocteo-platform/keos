"""SQLite-backed calibration store.

One row per `rule_id`. Schema chosen for the smallest set of fields that
answers the operator-facing question "should I trust the agent on this
rule?" + the graph-facing question "what threshold do I gate on?".

Storage location follows the same convention as `incident_store` +
`pipeline_trace`: derived from `settings.audit_log_path` parent, so the
calibration DB sits next to incidents/ + traces/ + the audit log.

Concurrency: the agent process has one async loop. We serialise writes
through a threading.Lock anyway because (a) SQLite serialises writes
internally and (b) the grader can be called from background threads
(retention loop, learning loops in Phase 16). Reads don't take the lock.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from agent.config import settings

log = logging.getLogger(__name__)


# ----- Schema --------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_stats (
    rule_id            TEXT PRIMARY KEY,
    n_total            INTEGER NOT NULL DEFAULT 0,
    n_correct          INTEGER NOT NULL DEFAULT 0,
    n_escalated_lowconf INTEGER NOT NULL DEFAULT 0,
    n_escalated_other   INTEGER NOT NULL DEFAULT 0,
    n_stale            INTEGER NOT NULL DEFAULT 0,
    n_override         INTEGER NOT NULL DEFAULT 0,
    sum_confidence     REAL    NOT NULL DEFAULT 0.0,
    -- Running p50/p90 estimates via a simple reservoir-sample over the
    -- last 200 confidence values. The reservoir lives in `samples_json`
    -- as a JSON list; quantiles re-derived on read. Cheap, exact for our
    -- N, and avoids a streaming-quantile dependency.
    samples_json       TEXT    NOT NULL DEFAULT '[]',
    -- Threshold override (operator-pinned). NULL = use learned value.
    threshold_override REAL,
    -- Last-updated. ISO 8601 UTC. Used for "stale rule" UI hint.
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
);
"""

# How many confidence samples we retain per rule for the p50/p90 estimate.
# 200 is plenty for stable percentile estimates without bloating the row.
SAMPLE_RESERVOIR_SIZE = 200


# ----- Data types ----------------------------------------------------------


@dataclass
class RuleStats:
    """Public projection of one rule_stats row.

    All counts are rolling totals since calibration was enabled (or
    since `reset()` was called). `accuracy` is computed from counts.
    `p50_confidence` / `p90_confidence` are quantiles over the last
    `SAMPLE_RESERVOIR_SIZE` confidence samples.
    """

    rule_id: str
    n_total: int = 0
    n_correct: int = 0
    n_escalated_lowconf: int = 0
    n_escalated_other: int = 0
    n_stale: int = 0
    n_override: int = 0
    sum_confidence: float = 0.0
    p50_confidence: float = 0.0
    p90_confidence: float = 0.0
    threshold_override: float | None = None
    samples: list[float] = field(default_factory=list)
    updated_at: str = ""

    @property
    def n_graded(self) -> int:
        """Incidents that contribute to the accuracy ratio.

        Excludes stale-resolved (the agent didn't actually diagnose) and
        operator-overrides (the operator made the call, not the agent).
        """
        return max(0, self.n_total - self.n_stale - self.n_override)

    @property
    def accuracy(self) -> float | None:
        """Fraction of graded incidents the agent got right. `None` when
        no graded data yet (so the UI doesn't show 0.0)."""
        if self.n_graded == 0:
            return None
        return self.n_correct / self.n_graded

    @property
    def mean_confidence(self) -> float | None:
        if self.n_total == 0:
            return None
        return self.sum_confidence / self.n_total

    @property
    def learned_threshold(self) -> float:
        """Threshold to gate on if there's no override.

        Heuristic: stay near the configured global `confidence_threshold`
        until we have at least 20 graded incidents. Then move toward
        the p10 of the rule's own correct-incident confidences, so a
        rule with consistently high confidence keeps a permissive bar
        and a rule with low confidence escalates sooner.

        We deliberately keep this conservative — a quick formula, easy
        to override, easy to explain in the UI.
        """
        global_default = float(settings.confidence_threshold)
        if self.n_graded < 20 or not self.samples:
            return global_default

        # Use 25th percentile of observed samples as the floor.
        # rule p25 = "we usually do at least this well; gate below it."
        # Clamped to [0.3, 0.9] so we never go too lenient or too strict.
        ordered = sorted(self.samples)
        idx = max(0, int(0.25 * len(ordered)) - 1)
        p25 = ordered[idx]
        return max(0.3, min(0.9, p25))

    @property
    def effective_threshold(self) -> float:
        """The value `_route_after_diagnose` will actually compare against.
        Override wins; otherwise learned; otherwise global default."""
        if self.threshold_override is not None:
            return self.threshold_override
        return self.learned_threshold


# ----- Store ---------------------------------------------------------------


class CalibrationStore:
    """Thread-safe SQLite wrapper. Stateless per-call: each method opens
    + closes its own connection so the store survives a fork."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as cx:
            cx.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        cx = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        # ROW factory makes rows dict-like, easier to project.
        cx.row_factory = sqlite3.Row
        # WAL = readers don't block writers, important for the SPA hitting
        # the read endpoint while the agent is grading an outcome.
        cx.execute("PRAGMA journal_mode = WAL;")
        cx.execute("PRAGMA synchronous = NORMAL;")
        return cx

    # ---- Public API ------------------------------------------------------

    def get(self, rule_id: str) -> RuleStats:
        """Read one rule's stats. Returns an empty-shell RuleStats if the
        rule has no row yet (a never-seen rule starts at zero, which is
        the right default)."""
        import json

        with self._connect() as cx:
            row = cx.execute(
                "SELECT * FROM rule_stats WHERE rule_id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            return RuleStats(rule_id=rule_id)
        samples = json.loads(row["samples_json"])
        stats = RuleStats(
            rule_id=row["rule_id"],
            n_total=row["n_total"],
            n_correct=row["n_correct"],
            n_escalated_lowconf=row["n_escalated_lowconf"],
            n_escalated_other=row["n_escalated_other"],
            n_stale=row["n_stale"],
            n_override=row["n_override"],
            sum_confidence=row["sum_confidence"],
            threshold_override=row["threshold_override"],
            samples=samples,
            updated_at=row["updated_at"],
        )
        stats.p50_confidence = _quantile(samples, 0.5)
        stats.p90_confidence = _quantile(samples, 0.9)
        return stats

    def list_all(self) -> list[RuleStats]:
        """All known rules. Used by the operator UI."""
        with self._connect() as cx:
            rows = cx.execute(
                "SELECT rule_id FROM rule_stats ORDER BY rule_id"
            ).fetchall()
        return [self.get(r["rule_id"]) for r in rows]

    def record_diagnose(self, rule_id: str, confidence: float) -> None:
        """Append a confidence sample. Called at diagnose-end with the
        LLM's reported confidence, regardless of whether it cleared the
        threshold or not. The histogram captures the distribution; the
        grader (called later) updates the correct/incorrect counts."""
        import json

        with self._lock, self._connect() as cx:
            # Upsert: create the row if missing, otherwise rotate the
            # reservoir + bump sum_confidence.
            cx.execute(
                """
                INSERT INTO rule_stats (rule_id, sum_confidence, samples_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    sum_confidence = sum_confidence + excluded.sum_confidence,
                    updated_at = excluded.updated_at
                """,
                (rule_id, float(confidence), json.dumps([float(confidence)]), _now_iso()),
            )
            # Update the reservoir on the existing row, capped at
            # SAMPLE_RESERVOIR_SIZE. Done as a separate UPDATE to avoid
            # rewriting the whole JSON on UPSERT conflicts.
            current = cx.execute(
                "SELECT samples_json FROM rule_stats WHERE rule_id = ?", (rule_id,)
            ).fetchone()
            if current is None:
                return
            samples: list[float] = json.loads(current["samples_json"])
            if float(confidence) not in samples[-1:]:  # avoid dup-on-upsert
                samples.append(float(confidence))
            if len(samples) > SAMPLE_RESERVOIR_SIZE:
                samples = samples[-SAMPLE_RESERVOIR_SIZE:]
            cx.execute(
                "UPDATE rule_stats SET samples_json = ?, updated_at = ? WHERE rule_id = ?",
                (json.dumps(samples), _now_iso(), rule_id),
            )

    def record_outcome(
        self,
        rule_id: str,
        outcome: str,  # 'healed' | 'escalated_lowconf' | 'escalated_other' | 'stale' | 'override'
    ) -> None:
        """Grade one terminal incident. Increments the right counter +
        bumps n_total. Idempotent only at the call-site level — callers
        should grade each incident exactly once (the grader module
        enforces this)."""
        column_map = {
            "healed": "n_correct",
            "escalated_lowconf": "n_escalated_lowconf",
            "escalated_other": "n_escalated_other",
            "stale": "n_stale",
            "override": "n_override",
        }
        col = column_map.get(outcome)
        if col is None:
            log.warning("record_outcome: unknown outcome %r; ignoring", outcome)
            return
        with self._lock, self._connect() as cx:
            cx.execute(
                f"""
                INSERT INTO rule_stats (rule_id, n_total, {col}, updated_at)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    n_total = n_total + 1,
                    {col} = {col} + 1,
                    updated_at = excluded.updated_at
                """,
                (rule_id, _now_iso()),
            )

    def set_threshold_override(self, rule_id: str, threshold: float | None) -> None:
        """Operator pin / unpin. `threshold=None` clears the override."""
        if threshold is not None:
            threshold = max(0.0, min(1.0, float(threshold)))
        with self._lock, self._connect() as cx:
            cx.execute(
                """
                INSERT INTO rule_stats (rule_id, threshold_override, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    threshold_override = excluded.threshold_override,
                    updated_at = excluded.updated_at
                """,
                (rule_id, threshold, _now_iso()),
            )

    def reset(self, rule_id: str | None = None) -> None:
        """Wipe stats. `rule_id=None` wipes everything (used by tests +
        the doctor's `make calibration-reset` recipe)."""
        with self._lock, self._connect() as cx:
            if rule_id is None:
                cx.execute("DELETE FROM rule_stats")
            else:
                cx.execute("DELETE FROM rule_stats WHERE rule_id = ?", (rule_id,))


# ----- Helpers -------------------------------------------------------------


def _quantile(samples: Iterable[float], q: float) -> float:
    """Simple linear-interpolation quantile. Returns 0.0 on empty input.
    Avoids pulling numpy for ~50 floats."""
    xs = sorted(samples)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ----- Default instance ----------------------------------------------------

_default: CalibrationStore | None = None


def get_default_store() -> CalibrationStore:
    """Singleton accessor. Path derived from `settings.audit_log_path`
    parent — same convention as `incident_store` + `pipeline_trace`."""
    global _default
    if _default is None:
        base = Path(settings.audit_log_path).parent
        _default = CalibrationStore(base / "calibration.db")
    return _default
