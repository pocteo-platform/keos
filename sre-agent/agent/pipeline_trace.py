"""LangGraph node instrumentation + in-memory trace bus + durable replay.

Three responsibilities:

1. `instrumented(name, fn)` wraps a graph node. On entry it emits a
   `pipeline.<node>.start` audit record + a TraceBus publish; on exit a
   `.end` record. Wraps every node uniformly so adding new nodes requires
   no extra plumbing.

2. `TraceBus` is an asyncio pub/sub keyed by `incident_id`. The
   WebSocket bridge subscribes; pipeline.start/end events fan out to
   browser clients.

3. Each trace record is also appended to `agent/traces/<incident_id>.jsonl`
   so the UI can REPLAY a finished incident later without keeping it in
   memory.

The wrapper is sync (graph nodes are sync); the bus is async. Sync code
schedules publishes via `loop.call_soon_threadsafe` if a loop is running,
otherwise it just writes the jsonl and skips the bus (e.g., during
`make demo` where there's no API process).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.audit import AuditLogger
from agent.config import settings

log = logging.getLogger(__name__)


# Per-incident traces live under `agent/traces/<incident_id>.jsonl`. We resolve
# this lazily so tests can override via env or monkeypatch on `settings`.
def _traces_dir() -> Path:
    base = Path(getattr(settings, "audit_log_path", "agent/audit.log"))
    return base.parent / "traces"


# ---------------------------------------------------------------------------
# TraceBus — in-memory pub/sub. Single instance per process.
# ---------------------------------------------------------------------------


class TraceBus:
    """Async pub/sub keyed by incident_id. Optionally also fans out to a
    catch-all `*` channel that receives every event (used by the
    `pipeline.*` WS channel for the "watch live" view)."""

    def __init__(self) -> None:
        # incident_id -> set[asyncio.Queue]
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._global: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """The serve.py orchestrator passes its loop in at startup so the
        sync `publish_threadsafe()` knows where to land events."""
        self._loop = loop

    # Active-incidents tracking — kept in-memory so /api/incidents/active can
    # answer cheaply. An incident is "active" once any `pipeline.*.start`
    # record has been written for it; it leaves the set when a `complete`
    # audit record fires (handled in serve.py) OR after a 10-minute TTL.
    _active: dict[str, float] = {}
    _ACTIVE_TTL_S = 600.0

    def mark_active(self, incident_id: str) -> None:
        self._active[incident_id] = time.monotonic()

    def mark_done(self, incident_id: str) -> None:
        self._active.pop(incident_id, None)

    def list_active(self) -> list[str]:
        cutoff = time.monotonic() - self._ACTIVE_TTL_S
        self._active = {k: v for k, v in self._active.items() if v >= cutoff}
        return list(self._active.keys())

    # ---- subscribe ------------------------------------------------------

    def subscribe(self, incident_id: str | None = None) -> TraceSubscription:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            if incident_id is None:
                self._global.add(q)
            else:
                self._subscribers[incident_id].add(q)
        return TraceSubscription(self, q, incident_id)

    def _drop(self, q: asyncio.Queue, incident_id: str | None) -> None:
        with self._lock:
            if incident_id is None:
                self._global.discard(q)
            else:
                self._subscribers.get(incident_id, set()).discard(q)

    # ---- publish --------------------------------------------------------

    def publish(self, incident_id: str, event: dict[str, Any]) -> None:
        """Async-context publish. Drops events for slow subscribers (no
        backpressure into the graph). Slow subscriber = bug; surface via log."""
        queues: list[asyncio.Queue] = []
        with self._lock:
            queues.extend(self._subscribers.get(incident_id, ()))
            queues.extend(self._global)
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("TraceBus subscriber queue full; dropping event")

    def publish_threadsafe(self, incident_id: str, event: dict[str, Any]) -> None:
        """Sync-context publish. Schedules onto the attached loop."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self.publish, incident_id, event)
        except RuntimeError:
            # Loop is closed (process shutdown). Drop quietly.
            pass


class TraceSubscription:
    """Async-iterable handle returned by TraceBus.subscribe()."""

    def __init__(self, bus: TraceBus, q: asyncio.Queue, incident_id: str | None) -> None:
        self._bus = bus
        self._q = q
        self._incident_id = incident_id
        self._closed = False

    async def __aiter__(self):
        while not self._closed:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    def close(self) -> None:
        self._closed = True
        self._bus._drop(self._q, self._incident_id)
        try:
            self._q.put_nowait(None)
        except asyncio.QueueFull:
            pass


# Module-level singleton (the serve.py orchestrator imports this).
bus = TraceBus()


# ---------------------------------------------------------------------------
# instrumented() — wraps a LangGraph node.
# ---------------------------------------------------------------------------

_audit = AuditLogger()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _snapshot(state: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Subset a state dict to the keys we want to ship to the UI. Keeps
    payload sizes sane even when graphs have a lot of accumulated context."""
    return {k: state.get(k) for k in keys if k in state}


def _write_trace_record(incident_id: str, record: dict[str, Any]) -> None:
    """Append one JSON line to agent/traces/<id>.jsonl."""
    path = _traces_dir() / f"{incident_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


# Fields we always copy from state on start/end for UI consumption.
_INPUT_KEYS = ("severity", "namespace", "pod", "rule", "agent_mode")
_OUTPUT_KEYS = (
    "diagnosis",
    "proposed_action",
    "policy_decision",
    "action_result",
    "verification",
    "escalated",
    "k8sgpt_findings",
    "context",
)


def instrumented(name: str, fn: Callable[[dict[str, Any]], dict[str, Any]]):
    """Decorator-style wrapper. Used by `agent/graph.py` at compile time."""

    @functools.wraps(fn)
    def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        incident_id = state.get("incident_id", "unknown")
        started = time.monotonic()
        start_record = {
            "ts": _now_iso(),
            "incident_id": incident_id,
            "node": name,
            "phase": "start",
            "input_snapshot": _snapshot(state, _INPUT_KEYS),
        }
        _write_trace_record(incident_id, start_record)
        _audit.emit(incident_id, f"pipeline.{name}.start", start_record)
        bus.publish_threadsafe(incident_id, start_record)
        bus.mark_active(incident_id)

        error: str | None = None
        try:
            update = fn(state) or {}
        except Exception as exc:  # noqa: BLE001 — record + rethrow
            error = repr(exc)
            update = {}
            raise
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            # Build the "what changed" view: just the keys this node added.
            merged_for_output = {**state, **update}
            end_record = {
                "ts": _now_iso(),
                "incident_id": incident_id,
                "node": name,
                "phase": "end",
                "elapsed_ms": elapsed_ms,
                "output_snapshot": _snapshot(merged_for_output, _OUTPUT_KEYS),
                "update_keys": list(update.keys()),
                "error": error,
            }
            _write_trace_record(incident_id, end_record)
            _audit.emit(incident_id, f"pipeline.{name}.end", end_record)
            bus.publish_threadsafe(incident_id, end_record)

            # Per-node Prometheus signal. The graph-level
            # `graph_run_duration_seconds` is recorded by serve.py once
            # the entire run finishes; here we just record LLM-bound
            # node latencies since those dominate. Other nodes are fast
            # enough that their histogram would just add noise.
            try:
                from agent import metrics as _m

                if name in {"diagnose", "plan_action"}:
                    _m.llm_call_duration_seconds.labels(step=name).observe(elapsed_ms / 1000.0)
            except Exception:  # noqa: BLE001 — metrics never break the graph
                pass

        return update

    return wrapper


# ---------------------------------------------------------------------------
# replay() — yields stored trace records for a finished incident.
# ---------------------------------------------------------------------------


def replay(incident_id: str) -> Iterator[dict[str, Any]]:
    """Yield trace records in chronological order for one incident."""
    path = _traces_dir() / f"{incident_id}.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def list_traced_incidents() -> list[str]:
    d = _traces_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))
