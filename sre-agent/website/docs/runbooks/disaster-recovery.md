---
id: disaster-recovery
slug: /runbooks/disaster-recovery
title: Disaster recovery
sidebar_position: 9
---

# Runbook: disaster recovery

## When to read this

- A cluster node is gone (hardware failure, accidental terraform destroy,
  cloud region outage).
- The Pocteo namespace was deleted (`kubectl delete ns pocteo`).
- All PVCs are wiped but you have a backup.
- The whole k3s/Talos cluster is unrecoverable but cluster-external
  backups exist.

If only ONE component is broken, see the component-specific runbook
instead — this one is the "rebuild from nothing" path.

## Pre-flight (1 min)

```bash
# Confirm you have a usable backup.
ls -la keos-*.tar.gz | tail -3
# OR for Velero:
velero backup get | grep pocteo

# Confirm the cluster you're restoring INTO is reachable.
kubectl cluster-info
kubectl get nodes

# Confirm the storage class is available.
kubectl get sc
```

If any of these fail, you're not ready — set them up first.

## Recovery time objectives

| Resource | RTO target | Notes |
|---|---|---|
| Cluster reachable | ~0 — must be true before this runbook | Out of scope |
| Keos deployed + healthy | 10 min | Helm install path |
| Audit chain restored + verified | 5 min | `tar xzf` + verify |
| Incidents + use cases restored | 5 min | Same tar |
| Qdrant rebuilt | 1 min | Re-seed from `agent/runbooks/*.md` |
| Ollama models | 5-10 min cold network / 0 if pre-pulled | `make pull-models` |
| **Total** | **~30 min** | If pre-pulled models, ~15 min |

## Path A — Velero (full restore)

```bash
# 1. List restore candidates.
velero backup get | grep pocteo

# 2. Restore. Use `--restore-volumes=true` to bring back PVCs.
BACKUP="pocteo-pocteo-daily-20260601020000"  # ← pick from step 1
velero restore create --from-backup "$BACKUP" \
  --restore-volumes=true \
  --include-namespaces pocteo

# 3. Watch progress.
velero restore describe <restore-name> --details

# 4. Once the api pod is Ready:
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
# All [OK] expected. If Qdrant is empty (`points=0`), re-seed:
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# 5. Set mode back to whatever it was pre-disaster.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"DR: velero restore"}}'

# 6. Verify the audit chain across all rotated files.
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f'{n} records, {len(bad)} tampered')
"
# Expect: 0 tampered. Any non-zero = the backup itself was tampered with
# OR the HMAC key changed; see audit-tampering.md.
```

## Path B — Manual (no Velero, just a tar)

```bash
# 1. Install Keos fresh.
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set-string auth.apiToken="$(openssl rand -base64 32)" \
    --set-string auth.hmacKey="<HMAC_KEY_FROM_BEFORE_DISASTER>" \
    --wait --timeout 10m

# IMPORTANT: use the SAME `auth.hmacKey` value that signed the old audit
# log. If you use a fresh key, verify_audit_log will mark the entire
# pre-disaster chain as invalid.
# If you don't have the old key, the chain is forensically dead — restore
# the data but expect tampered=N for every old line.

# 2. Wait for the api pod.
kubectl -n pocteo rollout status deploy/api --timeout=5m

# 3. Stop the agent before restoring volumes (otherwise writes race).
kubectl -n pocteo scale deploy/api --replicas=0

# 4. Restore the tar into the audit + data volumes.
POD=$(kubectl -n pocteo run dr-helper --image=alpine --command -- sleep 3600 -o name \
  --overrides='{
    "spec": {
      "volumes": [
        {"name":"audit","persistentVolumeClaim":{"claimName":"pocteo-audit"}},
        {"name":"data","persistentVolumeClaim":{"claimName":"pocteo-data"}}
      ],
      "containers":[{
        "name":"dr-helper","image":"alpine","command":["sleep","3600"],
        "volumeMounts":[
          {"name":"audit","mountPath":"/audit"},
          {"name":"data","mountPath":"/data"}
        ]
      }]
    }
  }')

kubectl -n pocteo exec -i "$POD" -- tar xzf - -C / < keos-backup.tar.gz
kubectl -n pocteo delete "$POD"

# 5. Bring the api back online.
kubectl -n pocteo scale deploy/api --replicas=1
kubectl -n pocteo rollout status deploy/api

# 6. Re-seed Qdrant (cheaper than restoring a Qdrant PVC).
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# 7. Restore the AgentMode CR if it was lost.
kubectl apply -f agentmode-backup.yaml

# 8. Verify everything.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f'{n} records, {len(bad)} tampered')
"
```

## Path C — Truly nothing left (no backup either)

Accept the loss of forensic continuity. The audit chain from before the
disaster is gone. Going forward:

```bash
# 1. Fresh install with NEW HMAC key. The new chain is independent.
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set-string auth.apiToken="$(openssl rand -base64 32)" \
    --set-string auth.hmacKey="$(openssl rand -base64 32)" \
    --wait --timeout 10m

# 2. Models + RAG.
kubectl -n pocteo exec deploy/api -- python -m agent.pull_models
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# 3. The library use cases (lib-memory-leak, lib-crashloop-recovery,
#    lib-node-pressure, lib-drain-low-severity) auto-seed on first
#    /api/usecases list call. Any operator-authored use cases are gone.

# 4. Restore mode.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"DR: fresh install, no backup"}}'

# 5. Document the gap in the postmortem.
```

## Verification — every restore path ends here

```bash
# A. Doctor.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor

# B. End-to-end use-case run.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run
# Expect: incident_id returned. Open /pipeline/<id>; the graph should
# complete in 4-5 min CPU / 25 s GPU.

# C. Audit chain.
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
print(verify_audit_log(settings.audit_log_path, settings.audit_hmac_key))
"

# D. If your alerts include `keos_*` metrics, give Prometheus 5 min to
#    rediscover the ServiceMonitor and start scraping again.
```

## Postmortem template

Disaster recovery postmortems have an extra section vs the standard
template (see [agent-api-down.md](agent-api-down.md#postmortem-template)):

- **What we lost.** Time range of audit records that didn't survive,
  count of incidents lost, list of operator-authored use cases that
  weren't backed up.
- **Why we lost it.** Root cause for the disaster itself (was the
  backup also a victim? was it not running? was it on the same blast
  radius?).
- **RTO actual vs target.** If you missed your 30-min target, what was
  the slow step?
- **Improvements.** Move backups off the same cluster. Test the restore
  path quarterly (see `BACKUP_RESTORE.md::Restore drill`).
