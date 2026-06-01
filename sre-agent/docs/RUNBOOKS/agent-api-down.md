# Runbook: agent API down

## Symptom

- `kubectl -n pocteo get pod -l app.kubernetes.io/component=api` shows
  `0/1 Running`, `CrashLoopBackOff`, or `Pending`.
- `/api/health` returns 5xx or times out from inside the cluster.
- The SPA login page fails token verification (the SPA hits `/api/incidents`
  to validate; 5xx fools it into "token rejected").
- K8s liveness probe failing — see `kubectl describe pod` for the count.

## Triage (5 min)

```bash
# 1. Pod state.
kubectl -n pocteo describe pod -l app.kubernetes.io/component=api | tail -40

# 2. Recent logs.
kubectl -n pocteo logs deploy/api --tail=80

# 3. Component health from a sister pod.
kubectl -n pocteo exec deploy/mcp-server -- \
  wget -q -O- http://api:8000/api/health || echo "api unreachable"

# 4. Doctor (if any pod responds).
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor 2>&1 || true
```

Common patterns:

| Log pattern | Likely cause | Jump to |
|---|---|---|
| `OSError: [Errno 28] No space left on device` | Audit volume full | Resolution §A |
| `kubernetes.client.exceptions.ApiException: ... Forbidden` | RBAC broke after upgrade | Resolution §B |
| `MaxRetryError ... host='host.docker.internal:6550'` (laptop) | Kubeconfig host unresolvable | Resolution §C |
| `Error: 401` from ModeStore watch in tight loop | Bearer-token rotation mid-flight | Resolution §D |
| Pod CrashLoopBackOff w/ `liveness probe failed` | Slow first-boot (Ollama warmup) — see startup probe | Resolution §E |
| `OllamaUnavailable: ollama unreachable after 3 attempts` in tight loop | Ollama is the actual problem | [ollama-down.md](ollama-down.md) |

## Containment

```bash
# Flip mode to frozen so OPA denies all actions while you investigate.
# The agent's reads (cluster view, audit) keep working; only writes stop.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"frozen","note":"runbook: agent-api-down"}}'
```

If the api pod is fully down, you don't need this — OPA already denies
without an agent to even call it. But flipping for the record makes the
audit trail show intent.

## Resolution

### §A Audit volume full

```bash
# Check disk on the audit volume.
kubectl -n pocteo exec deploy/api -- df -h /audit /data

# Inspect the offenders.
kubectl -n pocteo exec deploy/api -- \
  du -sh /audit/audit.log* /audit/incidents /audit/traces | sort -h

# Lower retention dramatically + bounce so the pruner runs immediately.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set retention.auditDays=7 \
  --set retention.incidentDays=14 \
  --set retention.traceDays=7 \
  --set retention.sweepIntervalS=300

# Force one immediate sweep:
kubectl -n pocteo exec deploy/api -- python -c "
from agent import incident_store
print('incidents:', incident_store.prune_old_incidents(14))
print('traces:',    incident_store.prune_old_traces(7))
"
```

### §B RBAC broke after upgrade

```bash
# Compare current RBAC against the chart.
kubectl get clusterrole pocteo-agent -o yaml > /tmp/current.yaml
helm template pocteo deploy/helm/pocteo --reuse-values \
  | yq 'select(.kind == "ClusterRole" and .metadata.name == "pocteo-agent")' \
  > /tmp/expected.yaml
diff /tmp/current.yaml /tmp/expected.yaml

# Re-apply.
helm upgrade pocteo deploy/helm/pocteo --reuse-values --force
```

### §C Kubeconfig host unresolvable (laptop only)

```powershell
# The api container's bind mount points at a stale file. Regenerate it.
.\make.ps1 kubeconfig
.\make.ps1 rebuild
```

See [USER_GUIDE.md §10](../USER_GUIDE.md) for the full kubeconfig-host
discussion; this is a known laptop trap.

### §D Bearer-token rotation in flight

The token file was overwritten mid-request. Wait 60 s for kubelet's
projected-volume refresh + re-read by api_auth, OR force-restart:

```bash
kubectl -n pocteo rollout restart deploy/api
kubectl -n pocteo rollout status  deploy/api
```

### §E Startup probe firing too early

```bash
# Inspect the startup probe failure count.
kubectl -n pocteo describe pod -l app.kubernetes.io/component=api \
  | grep -A 3 "Startup:"

# If the model is still warming on first boot, bump failureThreshold.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set api.probes.startup.failureThreshold=120  # 10 min instead of 5
```

## Postmortem template

```markdown
# Postmortem: agent-api-down YYYY-MM-DD

## Impact
- Duration: <start> → <end>
- During the outage: <which API surfaces unreachable; did any K8s probes fail; did `chaos_observer` queue any events that piled up?>

## Timeline
- HH:MM — first symptom observed (alert / page)
- HH:MM — triage step that surfaced the root cause
- HH:MM — containment applied (mode frozen)
- HH:MM — resolution applied
- HH:MM — `/api/health` 200 again, doctor green

## Root cause
<one paragraph>

## What we did right
<bullets>

## What we'll change
<bullets — runbook updates, alert tuning, code changes>

## Action items
- [ ] Owner — by date — description
```
