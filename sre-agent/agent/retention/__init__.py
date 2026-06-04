"""Phase C — operator-tunable retention windows.

Three windows govern how long the agent keeps its on-disk artifacts:

  - `auditDays`     audit.log rotated files
  - `incidentDays`  per-incident JSON snapshots
  - `traceDays`     per-incident pipeline trace JSONLs

By default these come from env vars (set via the Helm chart's
retention.* values). The operator can ALSO tune them live from the
SPA's Settings → Data Lifecycle tab; live values are persisted to
`/data/retention.json` and survive a Helm re-roll (the overlay wins
over the env defaults).

The retention pruner reads `RetentionStore.get()` on each iteration
so a live edit takes effect at the next sweep (≤ `RETENTION_SWEEP_INTERVAL_S`,
default one hour) — no pod restart needed.
"""

from agent.retention.store import RetentionConfig, RetentionStore, get_default_store

__all__ = ["RetentionConfig", "RetentionStore", "get_default_store"]
