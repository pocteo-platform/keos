# Runbook: audit log tampering / HMAC verification failure

## Severity

**High.** The audit chain is the agent's forensic guarantee. A non-zero
`indices_of_invalid` from `verify_audit_log()` means either:

1. The file was modified after writing (deliberate or accidental).
2. The HMAC key was rotated without re-signing the chain.
3. Filesystem corruption.

Treat case 1 as a security event until proven otherwise.

## Symptom

- `kubectl -n pocteo exec deploy/api -- python -c "
  from agent.audit import verify_audit_log; from agent.config import settings;
  print(verify_audit_log(settings.audit_log_path, settings.audit_hmac_key))"`
  returns a non-empty list of invalid line indices.
- An alert fired from a scheduled chain-verification cron (recommended,
  see "Going forward" below).
- The Incidents detail → Audit chain tab shows a red ✗ chip on one or
  more records.

## Triage (5 min)

```bash
# 1. Run the verifier; capture the exact bad indices.
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f'{n} records total, {len(bad)} invalid')
print('indices:', bad[:20])
"

# 2. Inspect the bad lines (DO NOT EDIT).
kubectl -n pocteo exec deploy/api -- bash -c "
  for i in 5 12 47; do  # ← bad indices from step 1
    sed -n \"\$((i+1))p\" /audit/audit.log
  done
"

# 3. File mtime sanity — the verifier always sees the same file the agent writes.
kubectl -n pocteo exec deploy/api -- ls -la /audit/audit.log /audit/audit.log.* 2>/dev/null

# 4. Was the HMAC key rotated recently?
kubectl -n pocteo get secret pocteo-pocteo-auth -o yaml | grep AUDIT_HMAC
```

| Pattern | Likely cause | Branch |
|---|---|---|
| All lines in the active file are invalid; rotated files clean | HMAC key was rotated without re-signing | Resolution §A |
| One isolated index, line looks edited (truncated, JSON malformed) | Deliberate or accidental edit | Resolution §B |
| Many lines invalid in a contiguous block | Disk corruption | Resolution §C |
| Active file lines invalid + content has unexpected bytes | Encoding issue (rare; the writer uses utf-8) | Resolution §D |

## Containment

**Lock the agent down immediately.**

```bash
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"frozen","note":"runbook: audit-tampering — investigating"}}'

# Snapshot the audit volume for forensics BEFORE doing anything else.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo exec deploy/api -- \
  tar czf - -C / audit > "audit-evidence-${TS}.tar.gz"
sha256sum "audit-evidence-${TS}.tar.gz" > "audit-evidence-${TS}.sha256"
# Store both files off-host (S3, encrypted USB, your forensic
# evidence locker — wherever your security policy requires).
```

The snapshot is the source of truth for any subsequent investigation.
Modifying the live file after this point destroys evidence.

## Resolution

### §A Key rotation without re-signing

```bash
# Was the key change intentional? If yes, the OLD chain is intact;
# you just need to verify with the OLD key.
OLD_KEY="..."  # retrieve from your secret manager's history

kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
n, bad = verify_audit_log('/audit/audit.log.2026-05-31', '$OLD_KEY')
print(f'with old key: {n} records, {len(bad)} invalid')
"
# Expect 0 invalid.

# Going forward, rotate keys via the documented flow in
# docs/RUNBOOKS/secret-rotation.md which preserves the chain.
```

### §B Deliberate or accidental edit

This is a **security incident**. Steps:

1. **Preserve evidence** — the snapshot from Containment is your
   primary artifact.
2. **Identify the access vector**:
   ```bash
   # Who has shell access to the api pod?
   kubectl auth can-i --list --as=system:authenticated -n pocteo \
     | grep -E "pod/exec|pod/portforward"

   # Recent kubectl exec events (requires audit logging on the K8s API,
   # which may or may not be enabled).
   kubectl get events --all-namespaces --sort-by=.lastTimestamp \
     | grep -E "exec|api-token"
   ```
3. **Quantify scope** — which incident_ids does the broken line set
   touch? An attacker who modified the audit may also have done
   something visible:
   ```bash
   kubectl -n pocteo exec deploy/api -- bash -c "
     for i in <bad indices>; do
       sed -n \"\$((i+1))p\" /audit/audit.log | jq -r .incident_id
     done | sort -u
   " > suspect-incidents.txt
   ```
4. **Rotate ALL secrets** — both API token and HMAC key.
   See [secret-rotation.md](secret-rotation.md).
5. **File a CVE-style internal advisory** describing the access vector
   and the timeline.

### §C Filesystem corruption

```bash
# Check the underlying volume.
kubectl -n pocteo describe pvc <audit-pvc>
kubectl -n pocteo exec deploy/api -- dmesg 2>/dev/null | tail -20 || true
# If using a CSI with snapshots, restore the last known-good snapshot.

# If no recoverable snapshot: the corrupt block is gone, document the
# loss in the postmortem. Note that the chain BEFORE the corruption is
# still valid — verify_audit_log returns indices, not "everything is
# wrong".
```

### §D Encoding / line-ending mishap (rare)

The agent writes with `open(..., "a", encoding="utf-8")`. If somebody
edited the file from Windows + got CRLF line endings inserted, the
canonical JSON re-serialization at verify time won't match. Strip the
CRLFs:

```bash
kubectl -n pocteo exec deploy/api -- \
  bash -c 'sed -i "s/\r//g" /audit/audit.log'

# Re-verify — should now be clean, OR if there were genuine modifications
# layered on top of the CRLF, the genuine ones will still flag.
```

## After the fix

```bash
# Full re-verify.
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(n, 'records,', len(bad), 'invalid')
"
# Expect: 0 invalid.

# Bring back to active only after a full root-cause is documented.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: audit-tampering resolved"}}'
```

## Going forward — recommended monitoring

Schedule a verification cron that pages on any non-zero invalid count:

```yaml
# k8s CronJob example. Drop into your monitoring namespace.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: keos-audit-verify
  namespace: pocteo
spec:
  schedule: "0 * * * *"   # hourly
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          serviceAccountName: pocteo-agent
          containers:
            - name: verify
              image: ghcr.io/your-org/pocteo-api:1.0.0-rc.1
              command:
                - python
                - -c
                - |
                  import sys
                  from agent.audit import verify_audit_log
                  from agent.config import settings
                  n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
                  if bad:
                      print(f"INVALID: {len(bad)} of {n}: {bad[:10]}")
                      sys.exit(1)
                  print(f"OK: {n} records, all valid")
              envFrom:
                - secretRef:
                    name: pocteo-pocteo-auth
              volumeMounts:
                - { name: audit, mountPath: /audit, readOnly: true }
          volumes:
            - name: audit
              persistentVolumeClaim:
                claimName: pocteo-audit
```

Alert on the Job's failure count via `kube_job_status_failed > 0`.

## Postmortem template

For security incidents, use your org's standard incident-postmortem
template (not the lightweight one in [agent-api-down.md](agent-api-down.md)).
Key sections to include:

- **Attack surface analysis** — how access was gained, what was modified.
- **Data integrity scope** — which incident_ids / time ranges are now
  untrusted. Note: ONLY the bad lines are untrusted; the rest of the
  chain remains valid.
- **Notification** — internal stakeholders informed; external
  regulatory notification if the affected incidents touched in-scope
  data.
- **Detection delta** — time from edit to detection. If > 1 hour, tune
  the verification cron + alert.
