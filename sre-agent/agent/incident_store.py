"""Persistence layer for finished incidents.

One JSON file per incident under `agent/incidents/<id>.json`. Cheap, no
DB, survives `make down/up`. The Phase-1-6 graph already produces all
the fields we want; we just persist the final state at `complete` time.

The store is purely additive — a new incident_id always means a new
file. We never edit an existing one.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.config import settings

log = logging.getLogger(__name__)


def _store_dir() -> Path:
    base = Path(getattr(settings, "audit_log_path", "agent/audit.log"))
    return base.parent / "incidents"


_lock = threading.Lock()


def save(incident_id: str, final_state: dict[str, Any]) -> Path:
    """Persist the final IncidentState plus a `saved_at` timestamp."""
    d = _store_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{incident_id}.json"
    body = {**final_state, "saved_at": _now_iso()}
    with _lock:
        path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return path


def load(incident_id: str) -> dict[str, Any] | None:
    path = _store_dir() / f"{incident_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_all() -> list[dict[str, Any]]:
    """Return every stored incident, newest first. Cheap for the PoC; a
    real product would index by ts in a DB."""
    d = _store_dir()
    if not d.exists():
        return []
    items: list[dict[str, Any]] = []
    for p in d.glob("*.json"):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    items.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return items


def delete(incident_id: str) -> bool:
    """Remove an incident's JSON file AND its trace JSONL. Returns True if
    the incident file existed (trace file may or may not — still True)."""
    d = _store_dir()
    inc_path = d / f"{incident_id}.json"
    trace_path = d.parent / "traces" / f"{incident_id}.jsonl"
    existed = inc_path.exists()
    with _lock:
        try:
            if inc_path.exists():
                inc_path.unlink()
        except OSError as exc:
            log.warning("incident_store.delete: failed to remove %s: %s", inc_path, exc)
            return False
        # Trace cleanup is best-effort — the audit log still has every
        # record so nothing's truly lost.
        try:
            if trace_path.exists():
                trace_path.unlink()
        except OSError as exc:
            log.warning("incident_store.delete: failed to remove trace %s: %s", trace_path, exc)
    return existed


def delete_many(incident_ids: list[str]) -> dict[str, int]:
    """Bulk delete. Returns a count summary."""
    deleted = 0
    missing = 0
    for inc_id in incident_ids:
        if delete(inc_id):
            deleted += 1
        else:
            missing += 1
    return {"deleted": deleted, "missing": missing}


def prune_old_incidents(max_age_days: int) -> int:
    """Delete incident JSON files older than `max_age_days`.

    Age is measured against the file's mtime — which we set on every write,
    so it matches the `saved_at` field. Returns the number of files removed.

    `max_age_days <= 0` is a no-op (operators who want unlimited retention
    set the env var to 0).

    Best-effort: errors on individual unlinks are logged but don't abort the
    sweep. The next periodic call retries.
    """
    if max_age_days <= 0:
        return 0
    d = _store_dir()
    if not d.exists():
        return 0
    cutoff = datetime.now(UTC).timestamp() - max_age_days * 86400
    removed = 0
    for p in d.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                if delete(p.stem):  # use the public delete() so traces go too
                    removed += 1
        except OSError as exc:
            log.warning("prune_old_incidents: stat %s failed (%s)", p, exc)
    if removed:
        log.info(
            "prune_old_incidents: removed %d incident(s) older than %d days", removed, max_age_days
        )
    return removed


def prune_old_traces(max_age_days: int) -> int:
    """Delete trace JSONL files older than `max_age_days`. Traces live next
    to incidents (`<audit_dir>/traces/<id>.jsonl`) and survive incident
    deletion only if the incident JSON was removed manually. This sweeps
    orphans too. Returns the count removed."""
    if max_age_days <= 0:
        return 0
    base = Path(getattr(settings, "audit_log_path", "agent/audit.log")).parent
    traces_dir = base / "traces"
    if not traces_dir.exists():
        return 0
    cutoff = datetime.now(UTC).timestamp() - max_age_days * 86400
    removed = 0
    for p in traces_dir.glob("*.jsonl"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError as exc:
            log.warning("prune_old_traces: unlink %s failed (%s)", p, exc)
    if removed:
        log.info(
            "prune_old_traces: removed %d trace file(s) older than %d days", removed, max_age_days
        )
    return removed


def summarise(incident: dict[str, Any]) -> dict[str, Any]:
    """Project a full IncidentState into the IncidentSummary shape."""
    diag = incident.get("diagnosis") or {}
    pa = incident.get("proposed_action") or {}
    pol = incident.get("policy_decision") or {}
    ver = incident.get("verification") or {}
    raw = incident.get("raw_event") or {}
    return {
        "incident_id": incident.get("incident_id", ""),
        "ts": incident.get("saved_at") or raw.get("time") or _now_iso(),
        "rule": incident.get("rule") or raw.get("rule", ""),
        "severity": incident.get("severity", "medium"),
        "namespace": incident.get("namespace"),
        "pod": incident.get("pod"),
        "diagnosis_summary": diag.get("summary"),
        "proposed_tool": pa.get("tool"),
        "policy_allow": pol.get("allow"),
        "healed": ver.get("healed") if ver else None,
        "escalated": bool(incident.get("escalated", False)),
        "agent_mode": incident.get("agent_mode"),
        "stale_resolved": bool(incident.get("stale_resolved", False)),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filter_and_paginate(
    items: list[dict[str, Any]],
    namespace: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    since: str | None = None,
    pod: str | None = None,
    rule: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """Apply the filters that the /api/incidents endpoint exposes.

    Phase 14.5 adds `pod` + `rule` so the workload drill-down view can
    pull only the incidents that touched a specific pod or a specific
    rule. The match is on `state["pod"]` / `state["rule"]` — exact
    string equality (not a regex), which is what the operator clicks
    on from the workload header.
    """

    def keep(it: dict[str, Any]) -> bool:
        if namespace and it.get("namespace") != namespace:
            return False
        if severity and it.get("severity") != severity:
            return False
        if pod and it.get("pod") != pod:
            return False
        if rule and it.get("rule") != rule:
            return False
        if status:
            esc = bool(it.get("escalated"))
            healed = bool((it.get("verification") or {}).get("healed"))
            if status == "healed" and not healed:
                return False
            if status == "escalated" and not esc:
                return False
            if status == "failed" and (esc or healed):
                return False
        if since and (it.get("saved_at") or "") < since:
            return False
        return True

    filtered = [i for i in items if keep(i)]
    total = len(filtered)
    return total, filtered[offset : offset + limit]
