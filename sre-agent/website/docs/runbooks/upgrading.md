---
id: upgrading
slug: /runbooks/upgrading
title: Upgrading Keos
sidebar_position: 8
---

# Runbook: upgrading Keos

## When to read this

- Before any `helm upgrade pocteo …` against a production cluster.
- After a `git pull` on a checkout that's about to become production.
- Before bumping the Helm chart version pin in a GitOps repo.

## Pre-upgrade checklist (15 min)

```bash
# 1. Doctor green on the current install.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
# All [OK]; fix any [--] FIRST.

# 2. Take a backup. The 5-step manual recipe in docs/BACKUP_RESTORE.md
#    takes ~2 min. The Velero path is one command if you've already set
#    up `backup.velero.enabled=true`.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo patch agentmode default --type=merge \
  -p '{"spec":{"mode":"frozen","note":"pre-upgrade backup"}}'
kubectl -n pocteo exec deploy/api -- tar czf - -C / audit data \
  > "keos-pre-upgrade-${TS}.tar.gz"
kubectl get agentmode default -o yaml > "agentmode-${TS}.yaml"

# 3. Read the CHANGELOG for the version you're going to.
git log --pretty=format:"%h %s" "$(helm ls -n pocteo -o json | jq -r '.[0].chart')..HEAD" \
  -- CHANGELOG.md
# OR: open CHANGELOG.md and read the section for the target version.

# 4. Snapshot current values for diffing afterwards.
helm get values pocteo -n pocteo > "values-before-${TS}.yaml"

# 5. Dry-run the upgrade.
helm upgrade pocteo deploy/helm/pocteo --reuse-values --dry-run \
  | diff <(helm get manifest pocteo -n pocteo) - \
  | head -100
# Read the diff. Reject the upgrade if it changes anything you didn't expect.
```

## Performing the upgrade

```bash
# 6. Apply.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --wait --timeout 10m

# 7. Wait for rollout to complete.
kubectl -n pocteo rollout status deploy/api deploy/mcp-server deploy/frontend
kubectl -n pocteo rollout status sts/ollama  sts/qdrant       sts/nats

# 8. Verify.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
# All [OK] expected. If anything is [--], proceed to "Rollback" below.

# 9. Smoke-test a use case.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth \
  -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run
# Watch /pipeline/<id> in the SPA — graph should complete cleanly.

# 10. Restore mode if you froze in step 2.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"upgrade complete"}}'
```

## Rollback

```bash
# Helm tracks revisions. Roll back to the previous one.
helm history pocteo -n pocteo
helm rollback pocteo <revision-1> -n pocteo --wait

# Verify.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

If `helm rollback` doesn't restore state (e.g., schema migration in the
new version mutated incident JSONs that the old code can't parse):

```bash
# Restore from the backup taken in step 2.
kubectl -n pocteo scale deploy/api --replicas=0
POD=$(kubectl -n pocteo run restore-helper --image=alpine --command -- sleep 3600 \
  --rm=false -o name)
# (Mount the audit + data PVCs into the helper pod; see BACKUP_RESTORE.md.)
cat "keos-pre-upgrade-${TS}.tar.gz" \
  | kubectl -n pocteo exec -i "$POD" -- tar xzf - -C /

kubectl -n pocteo scale deploy/api --replicas=1
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

## Breaking-change matrix

The CHANGELOG flags breaking changes per release. Common categories
(read carefully when one of these is in your upgrade path):

| Category | What to watch | Mitigation |
|---|---|---|
| Action catalog rename (e.g. `isolate_pod` → `uncordon_node`) | Stale incident JSON files reference old tool names | `agent/incident_store.py::summarise` already skips invalid; manual cleanup possible via the Incidents UI's bulk-delete |
| `IncidentSummary` schema fields added | Frontend old; backend new — UI shows blank in new columns | Rebuild the frontend along with the backend |
| OPA matrix re-keyed | `policies/agent.rego` changed; old persisted approvals may reference dropped actions | Approve / deny the queue before upgrading; the queue clears on `delete pod` (approvals are in-memory snapshotted) |
| AgentMode CRD version bump | `kubectl get agentmode default -o yaml` shows the old apiVersion | Apply the new CRD with `helm upgrade`; existing CRs auto-migrate (server-side schema is union of versions) |
| Helm `values.yaml` keys removed | `helm upgrade --reuse-values` errors with "unknown field" | Compute the diff (`helm template` from old vs new) and explicitly `--set key=value` for renames |

## After the upgrade

- Run a real scenario from the SPA (use case + chaos experiment) to
  exercise the full path including the LLM.
- Watch `kubectl -n pocteo logs deploy/api -f` for ~10 min to catch
  unexpected warnings.
- Check `keos_escalations_total` rate (Prometheus) — a sudden jump
  after upgrade suggests a regression.
- Re-run `make doctor` at +1 hr, +24 hr to catch slow-burn issues
  (retention loop misconfig, NATS consumer slowly falling behind, etc.).

## Postmortem template (only if the upgrade caused a real incident)

Same shape as [agent-api-down.md](agent-api-down.md#postmortem-template).
Add:

- **Versions involved:** from-tag → to-tag.
- **What we expected vs what happened.** Compare against the CHANGELOG entry.
- **Did the pre-upgrade checklist surface the problem?** If not, what
  check would have? File a PR adding it.
