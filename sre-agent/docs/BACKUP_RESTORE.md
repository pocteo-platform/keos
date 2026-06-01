# Backup & restore

Keos's state lives in a small, well-defined set of locations. Backing up and
restoring all of them is a documented one-command flow with Velero, or a
3-step manual flow with `kubectl cp`.

## What needs to be backed up

| Resource | Where it lives | Lose it = lose what? |
|---|---|---|
| **Audit log** | api pod, `/audit/audit.log` (+ rotated `audit.log.YYYY-MM-DD`) | Forensic chain. HMAC-signed; tampering is detectable. |
| **Incidents** | api pod, `/audit/incidents/<id>.json` | Past graph runs (Incidents view in the SPA). Reconstructable from the audit log in extremis. |
| **Pipeline traces** | api pod, `/audit/traces/<id>.jsonl` | Per-node start/end records for replay. Same recoverability as incidents. |
| **Use case definitions** | api pod, `/data/shp-usecases.json` | Operator-authored use cases. The 3 library entries auto-seed on empty file. |
| **Approvals queue** | api pod, `/audit/approvals/pending.json` | Outstanding L3/L4 denials waiting for human override. Lose this → operator has to wait for the next event to trigger the same scenario. |
| **AgentMode CR** | K8s cluster (`AgentMode/default`) | Current kill-switch state. Defaults to `observe-only` on restore — operator must flip back to `active` if that was the live state. |
| **Qdrant collection `runbooks`** | qdrant StatefulSet PVC | Vector embeddings for the 7 SRE runbooks. Rebuildable in 30 s via `make seed`. |
| **Ollama model weights** | ollama StatefulSet PVC | ~2 GB of model. Not worth backing up — `make pull-models` re-fetches. |
| **NATS JetStream stream** | nats StatefulSet PVC | In-flight events the agent hasn't processed yet. Acceptable loss on most restores (cluster events will fire again). |

**Recovery time objectives:**

- Full restore from Velero snapshot: ~10 minutes (mostly PVC re-mount + pod boot).
- Manual restore from `kubectl cp` tarball: ~5 minutes.
- Rebuild from scratch (no backup, just `make install`): ~10 minutes including model pull.

---

## Path A — Velero (recommended for production)

### Prerequisites

Velero installed in the cluster, with an object storage backend
configured (S3 / GCS / Azure Blob / MinIO). See
<https://velero.io/docs/main/basic-install/> for the install — this chart
does NOT install Velero itself.

### Schedule a daily backup

Enable the Velero `Schedule` CR rendered by the chart:

```bash
helm upgrade --install pocteo deploy/helm/pocteo \
  --set backup.velero.enabled=true \
  --set backup.velero.schedule="0 2 * * *" \
  --set backup.velero.ttl="168h" \
  --set backup.velero.storageLocation="default"
```

Verify it's scheduling:

```bash
kubectl -n pocteo get schedules.velero.io
velero schedule get
```

### Restore

```bash
# List available backups.
velero backup get | grep pocteo-pocteo-daily

# Restore into a fresh namespace (recommended) or replace in place.
velero restore create --from-backup pocteo-pocteo-daily-20260601020000 \
  --restore-volumes=true

# Watch the restore progress.
velero restore describe <restore-name> --details

# Once the api pod is Ready, verify state:
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

If the restore lands the AgentMode CR in `observe-only` (the chart's
install default), flip it back:

```bash
kubectl patch agentmode default --type=merge \
  --patch-file deploy/helm/pocteo/files/mode-active.yaml
```

---

## Path B — Manual (laptop / quick checkpoint)

### Backup

```bash
# Pause incoming work briefly so the snapshot is consistent.
kubectl -n pocteo patch agentmode default --type=merge -p '{"spec":{"mode":"frozen"}}'

# Tar the api pod's data dirs.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo exec deploy/api -- \
  tar czf - -C / audit data \
  > "keos-backup-${TS}.tar.gz"

# Snapshot Qdrant + AgentMode CR (small text files).
kubectl -n pocteo exec qdrant-0 -- \
  curl -s http://localhost:6333/collections/runbooks/snapshots \
  -X POST > "qdrant-snapshot-${TS}.json"

kubectl get agentmode default -o yaml > "agentmode-${TS}.yaml"

# Resume work.
kubectl -n pocteo patch agentmode default --type=merge -p '{"spec":{"mode":"active"}}'
```

### Restore

```bash
# 1. Stop the api so nothing writes during restore.
kubectl -n pocteo scale deploy/api --replicas=0

# 2. Wipe the existing dirs + extract the backup.
POD=$(kubectl -n pocteo get pod -l app.kubernetes.io/component=api -o name | head -1)
# (If api scaled to 0, use a debug pod with the same PVCs instead.)
kubectl -n pocteo exec "$POD" -- rm -rf /audit/* /data/*
cat keos-backup-20260601T020000Z.tar.gz \
  | kubectl -n pocteo exec -i "$POD" -- tar xzf - -C /

# 3. Restore the AgentMode CR (if it was lost).
kubectl apply -f agentmode-20260601T020000Z.yaml

# 4. Re-seed Qdrant from runbook source files (cheaper than snapshot restore).
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# 5. Bring the api back online.
kubectl -n pocteo scale deploy/api --replicas=1

# 6. Verify.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

---

## Restore drill — verifying the procedure works

A backup you've never tested is theatre. Run this drill quarterly:

```bash
# 1. Take a backup of the current healthy state.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo exec deploy/api -- \
  tar czf - -C / audit data > "drill-${TS}.tar.gz"

# 2. Trigger a use case so there's recent state to lose.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
INCIDENT_ID=$(curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run | jq -r .incident_id)
# Wait ~5 min for the graph to finish on CPU.

# 3. Simulate disaster: wipe everything.
kubectl -n pocteo scale deploy/api --replicas=0
kubectl -n pocteo delete pvc data-ollama-0 data-qdrant-0 data-nats-0 || true
# (Recreate them by re-running helm upgrade.)
helm upgrade pocteo deploy/helm/pocteo \
  --set-string auth.apiToken=$(openssl rand -base64 32) \
  --set-string auth.hmacKey=$(openssl rand -base64 32) \
  --reuse-values

# 4. Restore from the backup.
POD=$(kubectl -n pocteo get pod -l app.kubernetes.io/component=api -o name | head -1)
cat "drill-${TS}.tar.gz" | kubectl -n pocteo exec -i "$POD" -- tar xzf - -C /
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# 5. Verify the pre-disaster incident is queryable.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost/api/incidents/${INCIDENT_ID}" | jq .incident_id
# Should print the incident_id from step 2.

# 6. Verify audit chain integrity.
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f'{n} records, {len(bad)} tampered')
"
# Expect: <N> records, 0 tampered.
```

If step 5 finds the incident AND step 6 reports 0 tampered, the restore
procedure works. Document the date in your runbook + diary the next drill.

---

## What's NOT backed up

- **Ollama model weights** (`ollama.persistence.size` ≈ 10 GB).
  Re-download with `kubectl -n pocteo exec deploy/api -- python -m
  agent.pull_models`. Saves a lot of backup storage at the cost of one
  10-minute pull on restore.

- **NATS in-flight messages.** JetStream persists them but cluster
  events that fire while we're restoring will trigger fresh graph runs
  anyway. Accepting this is cheaper than the consistency contortions
  needed to back up an active stream.

- **Chaos Mesh experiments mid-flight.** They auto-expire via
  `TTLSecondsAfterFinished` so a restore mid-experiment just leaves a
  stale CR that the controller cleans up.

---

## Retention defaults (Helm `retention.*`)

The agent prunes its own state to keep disks bounded:

| Setting | Default | What it prunes |
|---|---|---|
| `retention.auditDays` | 30 | `audit.log.YYYY-MM-DD` rotated siblings older than this |
| `retention.incidentDays` | 90 | `incidents/*.json` whose mtime is older than this |
| `retention.traceDays` | 30 | `traces/*.jsonl` whose mtime is older than this |
| `retention.sweepIntervalS` | 3600 | How often the pruner runs |

Set any of these to `0` to disable that sweep (forever-retention).
The HMAC audit chain stays verifiable across rotation — `verify_audit_log()`
walks every rotated file automatically.

For longer retention than the in-pod sweep allows, ship the log + incident
dirs to S3 / Loki / Datadog (see `docs/OBSERVABILITY.md` §Loki recipes).
