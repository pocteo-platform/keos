"""Append-only, HMAC-signed audit log.

Every state transition in the graph emits exactly one JSON line. Each line
carries an HMAC-SHA256 over (ts, incident_id, event_type, payload), keyed
with `AUDIT_HMAC_KEY` from .env. That key is a dev placeholder in this PoC;
on the Jetson target it would be sealed via TPM or systemd-creds.

Verification (offline): re-compute the HMAC for every line and compare.
A `verify_audit_log()` helper is provided for tests / operator tooling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from agent.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retention configuration.
#
# `AUDIT_LOG_RETENTION_DAYS` controls how many rotated files we keep. 0 = keep
# forever (still rotates daily so each file is bounded in size, but old days
# never get pruned — operators who need legal-grade retention rely on a
# downstream log shipper anyway).
# ---------------------------------------------------------------------------
def _retention_days() -> int:
    try:
        return max(0, int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sign(key: bytes, payload: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


class AuditLogger:
    """Thread-safe append-only HMAC audit emitter.

    Files rotate daily at midnight UTC via `logging.handlers.TimedRotatingFileHandler`.
    Rotated names are `audit.log.YYYY-MM-DD`. `AUDIT_LOG_RETENTION_DAYS` env
    controls how many rotated files we keep; 0 = keep forever.

    One process can hold one instance; concurrent emits serialise on a
    threading.Lock so the rotating handler's internal state stays
    consistent. This is fine for the PoC where the graph runs one
    incident at a time per process.
    """

    def __init__(self, path: str | None = None, key: str | None = None) -> None:
        self._path = Path(path or settings.audit_log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._key = (key or settings.audit_hmac_key).encode("utf-8")
        self._lock = threading.Lock()
        # Use TimedRotatingFileHandler for the file write so we get
        # midnight rotation + backup retention for free. We don't actually
        # use it as a Python logging handler — we call its private
        # `.shouldRollover()` + `.doRollover()` machinery to keep our
        # custom line format intact. (The standard logging.Logger would
        # wrap each line in its own LogRecord, which would defeat the
        # canonical-JSON HMAC.)
        self._handler = TimedRotatingFileHandler(
            self._path,
            when="midnight",
            interval=1,
            backupCount=_retention_days(),
            encoding="utf-8",
            utc=True,
            delay=True,  # don't open until first write
        )
        # Match the existing flat-text suffix shape so old `audit.log.1`
        # style rotated files (if any) coexist. The handler uses
        # strftime(suffix) → `audit.log.YYYY-MM-DD` by default.

    # ---- public API ------------------------------------------------------

    def emit(self, incident_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one signed event line. Returns the full record for callers
        that want to log or assert against it."""
        ts = _now_iso()
        body = {
            "ts": ts,
            "incident_id": incident_id,
            "event_type": event_type,
            "payload": payload,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body["hmac"] = _sign(self._key, canonical)
        line = json.dumps(body, separators=(",", ":"))
        with self._lock:
            # Roll over if midnight has passed, then write the raw line.
            if self._handler.shouldRollover(None):  # type: ignore[arg-type]
                self._handler.doRollover()
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        log.debug("audit.emit %s/%s -> %s", incident_id, event_type, self._path)
        return body

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        """Read the last N lines, parsed. Convenience for the demo printer."""
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        out: list[dict[str, Any]] = []
        for raw in lines[-n:]:
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out


def verify_audit_log(path: str, key: str) -> tuple[int, list[int]]:
    """Re-validate the HMAC of every line across the active file AND every
    rotated `audit.log.YYYY-MM-DD` sibling. Returns (n_records, indices_of_invalid).

    A non-empty `indices_of_invalid` list means the audit log was tampered
    with after writing (or the key changed). Indices are global across the
    concatenated stream (active file first, then rotated files in
    descending date order — newest rotated first).
    """
    p = Path(path)
    keyb = key.encode("utf-8")
    invalid: list[int] = []
    n = 0

    files = _ordered_audit_files(p)
    for fpath in files:
        with fpath.open("r", encoding="utf-8") as fh:
            for raw in fh:
                idx = n
                n += 1
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    invalid.append(idx)
                    continue
                claimed = rec.pop("hmac", None)
                if claimed is None:
                    invalid.append(idx)
                    continue
                canonical = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
                if not hmac.compare_digest(claimed, _sign(keyb, canonical)):
                    invalid.append(idx)
    return (n, invalid)


def _ordered_audit_files(active: Path) -> list[Path]:
    """Active file + every `audit.log.YYYY-MM-DD` rotated sibling, newest first.
    Returns [] when no files exist at all."""
    out: list[Path] = []
    if active.exists():
        out.append(active)
    if active.parent.exists():
        rotated = sorted(
            (p for p in active.parent.glob(f"{active.name}.*") if p.is_file()),
            reverse=True,
        )
        out.extend(rotated)
    return out
