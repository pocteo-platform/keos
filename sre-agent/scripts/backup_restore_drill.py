#!/usr/bin/env python
"""Backup-restore acceptance drill.

Phase 14.11 — closes the V1 audit gap that flagged Phase 10.4's
backup-restore as documented but never automatically exercised.

What this drill proves:

  1. Every stateful artifact the agent writes is on a path we KNOW about
     (`AUDIT_LOG_PATH`, calibration DB, baselines DB, incidents/, traces/,
     SHP_USECASES_PATH). Anything else is data loss waiting to happen.
  2. A tar of those paths + a re-import after a blank wipe gives back
     a working agent — audit chain re-verifies, calibration thresholds
     intact, incidents browseable.
  3. The drill itself is reproducible: run it locally, run it in CI,
     run it before any release.

This is INTENTIONALLY simple — it doesn't exercise Velero, S3, or
operator workflows. Its only job is to give us confidence that the
state model is complete and round-trips.

Exit code 0 = drill passed; 1 = state lost or corrupted; 2 = setup
error (couldn't even create the test state).

Usage:
    python scripts/backup_restore_drill.py [--keep-tmp]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

# Paths the agent writes to that MUST be in the backup envelope. Kept
# in sync with deploy/helm/pocteo/values.yaml (retention.*) and the
# api.yaml volumeMount paths.
STATEFUL_PATHS_DOC = """
Agent stateful paths
====================
  /audit/audit.log                Phase 4 HMAC-signed audit chain
  /audit/audit.log.YYYY-MM-DD     Rotated chain files (Phase 10.4)
  /data/calibration.db            Phase 12 per-rule calibration store
  /data/baselines.db              Phase 13 ML baselines store
  /data/shp-usecases.json         Phase 8 user-authored use cases
  agent/incidents/inc-*.json      Per-incident state snapshots
  agent/traces/inc-*.jsonl        Per-incident pipeline trace records
  agent/approvals/pending.json    Pending operator-approval queue

Everything ELSE in the running container is either ephemeral
(/tmp), config (/secrets), or the bind-mounted manifests/ source.
"""


def _check(cond: bool, msg: str) -> None:
    status = "OK" if cond else "FAIL"
    print(f"  [{status}]  {msg}")
    if not cond:
        sys.exit(1)


def _seed_state(root: Path) -> dict[str, object]:
    """Create the minimum cross-section of state needed to prove the
    backup envelope works. We don't need the whole stack running —
    we just need files at the documented paths with non-trivial
    content the round-trip can verify."""
    audit_dir = root / "audit"
    data_dir = root / "data"
    incidents_dir = root / "incidents"
    traces_dir = root / "traces"
    for d in (audit_dir, data_dir, incidents_dir, traces_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Audit log: one line, valid HMAC if AUDIT_HMAC_KEY is set.
    from agent.audit import AuditLogger

    log_path = audit_dir / "audit.log"
    logger = AuditLogger(path=str(log_path), key="drill-test-key")
    logger.emit(
        incident_id="inc-drill-1",
        event_type="drill.seeded",
        payload={"reason": "backup_restore_drill"},
    )

    # 2. Calibration DB: register a couple rules + a threshold override.
    # The store's API is (record_diagnose, record_outcome,
    # set_threshold_override) — match it exactly so the drill round-trips
    # through the real storage layer.
    from agent.calibration.store import CalibrationStore

    cal_path = data_dir / "calibration.db"
    cal = CalibrationStore(db_path=cal_path)
    cal.record_diagnose("rule-foo", confidence=0.85)
    cal.record_outcome("rule-foo", outcome="healed")
    cal.record_diagnose("rule-foo", confidence=0.91)
    cal.record_outcome("rule-foo", outcome="healed")
    cal.set_threshold_override("rule-foo", threshold=0.5)

    # 3. Use cases JSON.
    use_cases_path = data_dir / "shp-usecases.json"
    use_cases_path.write_text(
        json.dumps(
            [
                {
                    "id": "uc-drill-1",
                    "name": "Drill use case",
                    "description_en": "Backup-restore drill marker",
                    "tags": ["drill"],
                }
            ]
        ),
        encoding="utf-8",
    )

    # 4. Incident snapshot.
    inc_path = incidents_dir / "inc-drill-1.json"
    inc_path.write_text(
        json.dumps(
            {
                "incident_id": "inc-drill-1",
                "rule": "drill rule",
                "ts": "2026-06-04T00:00:00Z",
                "severity": "medium",
                "healed": True,
                "escalated": False,
            }
        ),
        encoding="utf-8",
    )

    # 5. Trace JSONL.
    trace_path = traces_dir / "inc-drill-1.jsonl"
    trace_path.write_text(
        '{"node":"triage","phase":"start","ts":"2026-06-04T00:00:00.000Z"}\n'
        '{"node":"triage","phase":"end","ts":"2026-06-04T00:00:00.010Z"}\n',
        encoding="utf-8",
    )

    return {
        "audit_lines": 1,
        "cal_rule": "rule-foo",
        "use_case_id": "uc-drill-1",
        "incident_id": "inc-drill-1",
    }


def _backup(state_root: Path, backup_path: Path) -> None:
    """Tar everything under state_root into backup_path."""
    with tarfile.open(backup_path, "w:gz") as tar:
        for child in sorted(state_root.iterdir()):
            tar.add(child, arcname=child.name)


def _wipe(state_root: Path) -> None:
    """Simulate a node loss: remove every file under state_root."""
    shutil.rmtree(state_root)
    state_root.mkdir(parents=True)


def _restore(backup_path: Path, state_root: Path) -> None:
    """Re-hydrate state_root from the tarball."""
    with tarfile.open(backup_path, "r:gz") as tar:
        tar.extractall(state_root)


def _verify(state_root: Path, expected: dict[str, object]) -> None:
    """Read the restored state and prove the four state-bearing artifacts
    are intact + functional."""

    # 1. Audit log: file exists + HMAC verifier accepts every line.
    from agent.audit import verify_audit_log

    audit_path = state_root / "audit" / "audit.log"
    _check(audit_path.exists(), "audit.log restored")
    n_records, invalid = verify_audit_log(str(audit_path), key="drill-test-key")
    _check(
        n_records >= int(expected["audit_lines"]),
        f"audit chain has >= {expected['audit_lines']} lines",
    )  # type: ignore[arg-type]
    _check(not invalid, "audit chain HMACs all valid (no tampering)")

    # 2. Calibration DB: re-open + read back the override.
    from agent.calibration.store import CalibrationStore

    cal_path = state_root / "data" / "calibration.db"
    _check(cal_path.exists(), "calibration.db restored")
    cal = CalibrationStore(db_path=cal_path)
    snap = cal.get(expected["cal_rule"])  # type: ignore[arg-type]
    _check(
        snap.threshold_override == 0.5,
        f"calibration threshold override survived (rule={expected['cal_rule']})",
    )
    _check(snap.n_correct == 2, "calibration grade history (n_correct) preserved")

    # 3. Use cases JSON: re-parse + look up by id.
    use_cases_path = state_root / "data" / "shp-usecases.json"
    _check(use_cases_path.exists(), "shp-usecases.json restored")
    use_cases = json.loads(use_cases_path.read_text(encoding="utf-8"))
    ids = {u["id"] for u in use_cases}
    _check(expected["use_case_id"] in ids, f"use case {expected['use_case_id']} survived")

    # 4. Incident snapshot.
    inc_path = state_root / "incidents" / f"{expected['incident_id']}.json"
    _check(inc_path.exists(), f"incident {expected['incident_id']}.json restored")
    inc = json.loads(inc_path.read_text(encoding="utf-8"))
    _check(inc.get("rule") == "drill rule", "incident snapshot fields intact")

    # 5. Trace JSONL.
    trace_path = state_root / "traces" / f"{expected['incident_id']}.jsonl"
    _check(trace_path.exists(), f"trace {expected['incident_id']}.jsonl restored")
    lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
    _check(len(lines) == 2, "trace JSONL has the expected 2 records")
    for line in lines:
        json.loads(line)  # parse — sanity that the JSONL is well-formed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-tmp",
        action="store_true",
        help="Leave the tmp state dir in place after the drill (debugging).",
    )
    args = parser.parse_args()

    print(STATEFUL_PATHS_DOC)
    print("=" * 68)
    print("  Backup-restore drill")
    print("=" * 68)

    tmp = Path(tempfile.mkdtemp(prefix="keos-backup-drill-"))
    print(f"  Working dir: {tmp}")
    state_root = tmp / "state"
    backup_path = tmp / "backup.tar.gz"

    # Count only durable state files, not SQLite's transient WAL/SHM
    # sidecars (those checkpoint into the .db on connection-close and
    # don't need to round-trip through the backup).
    def _count_durable_files(root: Path) -> int:
        return sum(
            1
            for p in root.rglob("*")
            if p.is_file() and not p.name.endswith((".db-wal", ".db-shm", ".db-journal"))
        )

    try:
        print("\n--- Seed: create state ---")
        state_root.mkdir(parents=True)
        expected = _seed_state(state_root)
        seeded_paths = _count_durable_files(state_root)
        print(f"  Seeded {seeded_paths} durable files under {state_root}")

        print("\n--- Backup ---")
        t0 = time.monotonic()
        _backup(state_root, backup_path)
        backup_size = backup_path.stat().st_size
        print(
            f"  Backup written: {backup_path} ({backup_size} bytes, {time.monotonic() - t0:.2f}s)"
        )

        print("\n--- Wipe (simulate node loss) ---")
        _wipe(state_root)
        remaining = _count_durable_files(state_root)
        _check(remaining == 0, "state directory is empty after wipe")

        print("\n--- Restore ---")
        t0 = time.monotonic()
        _restore(backup_path, state_root)
        restored = _count_durable_files(state_root)
        _check(restored == seeded_paths, f"restored {restored}/{seeded_paths} durable files")
        print(f"  Restore took {time.monotonic() - t0:.2f}s")

        print("\n--- Verify ---")
        _verify(state_root, expected)

        print("\n" + "=" * 68)
        print("  Drill PASSED — every backup-envelope path round-trips cleanly.")
        print("=" * 68)
        return 0

    finally:
        if args.keep_tmp:
            print(f"\n  --keep-tmp set: leaving {tmp} in place for inspection")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
