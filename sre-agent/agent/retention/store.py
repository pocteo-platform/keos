"""On-disk overlay store for retention windows.

Reads env vars on first call → caches the result → lets the API
write a live override that survives pod restarts via `/data/retention.json`.

Why an overlay and not just "edit values.yaml + helm upgrade":
  - Helm upgrade restarts the pod (~30s downtime on a single-replica
    install). Tuning retention from the UI shouldn't cause downtime.
  - Operators trying out different retention windows on a hot demo
    want immediate feedback; the next sweep tick (≤ 1 h) is enough.
  - When operators upgrade Keos via Helm, their UI-set values stay
    intact because the overlay file is in the PVC, not in the env.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class RetentionConfig:
    """The three retention windows + the sweep cadence.

    All durations in DAYS, except `sweep_interval_s` in seconds.
    A value of 0 for any day-count means 'keep forever' — the sweep
    skips that bucket entirely."""

    audit_days: int = 30
    incident_days: int = 90
    trace_days: int = 30
    sweep_interval_s: int = 3600


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning("retention: env %s=%r is not an integer; using default %d", name, raw, default)
        return default


def _config_from_env() -> RetentionConfig:
    """Read defaults from the same env vars the Helm chart sets."""
    return RetentionConfig(
        audit_days=_env_int("AUDIT_LOG_RETENTION_DAYS", 30),
        incident_days=_env_int("INCIDENT_RETENTION_DAYS", 90),
        trace_days=_env_int("TRACE_RETENTION_DAYS", 30),
        sweep_interval_s=_env_int("RETENTION_SWEEP_INTERVAL_S", 3600),
    )


class RetentionStore:
    """Thread-safe overlay store. One instance per process.

    Layout on disk:
        {data_root}/retention.json   # operator's override (optional)

    Precedence (highest first):
        1. Overlay file at {data_root}/retention.json
        2. Env vars (AUDIT_LOG_RETENTION_DAYS, etc.)
        3. Built-in defaults in RetentionConfig
    """

    def __init__(self, overlay_path: Path | str) -> None:
        self._path = Path(overlay_path)
        self._lock = threading.RLock()
        self._cache: RetentionConfig | None = None

    def get(self) -> RetentionConfig:
        """Fast: returns the cached config; loads from disk + env on
        the first call. Callers (especially the pruner) can call this
        every sweep without worrying about IO."""
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            return self._cache

    def put(self, cfg: RetentionConfig) -> RetentionConfig:
        """Persist + cache. Returns the saved config."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
            tmp.replace(self._path)
            self._cache = cfg
            log.info(
                "retention: overlay updated (audit=%d incident=%d trace=%d sweep=%ds)",
                cfg.audit_days,
                cfg.incident_days,
                cfg.trace_days,
                cfg.sweep_interval_s,
            )
            return cfg

    def reset_to_defaults(self) -> RetentionConfig:
        """Delete the overlay file → next get() returns env+defaults."""
        with self._lock:
            try:
                self._path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("retention: could not delete overlay %s (%s)", self._path, exc)
            self._cache = None
            return self.get()

    def _load(self) -> RetentionConfig:
        env_cfg = _config_from_env()
        if not self._path.exists():
            return env_cfg
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "retention: overlay %s unreadable (%s); falling back to env defaults",
                self._path,
                exc,
            )
            return env_cfg
        # The overlay may be partial — fill missing keys from env_cfg.
        return RetentionConfig(
            audit_days=int(data.get("audit_days", env_cfg.audit_days)),
            incident_days=int(data.get("incident_days", env_cfg.incident_days)),
            trace_days=int(data.get("trace_days", env_cfg.trace_days)),
            sweep_interval_s=int(data.get("sweep_interval_s", env_cfg.sweep_interval_s)),
        )


_default_store: RetentionStore | None = None
_default_lock = threading.Lock()


def get_default_store() -> RetentionStore:
    """Process-wide singleton. Path comes from `RETENTION_OVERLAY_PATH`
    (default `/data/retention.json` to match the Helm chart's PVC mount)."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            path = os.environ.get("RETENTION_OVERLAY_PATH", "/data/retention.json")
            _default_store = RetentionStore(path)
        return _default_store
