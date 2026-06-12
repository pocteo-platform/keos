# Runbook: secret rotation

Five rotatable secrets, each with a different blast radius. This runbook
covers all five.

## Inventory

| Secret | Env var | What an attacker gets if leaked | Rotation cadence (recommended) |
|---|---|---|---|
| **API bearer token** | `SHP_API_TOKEN` | Full SPA + REST API access | Every 90 days OR on operator turnover |
| **Audit HMAC key** | `AUDIT_HMAC_KEY` | Ability to forge audit entries undetected | Annually OR on breach suspicion |
| **Slack webhook** | `SLACK_WEBHOOK_URL` | Can post arbitrary messages to your incident channel | On leak; otherwise indefinite |
| **Generic page webhook** | `PAGE_HUMAN_WEBHOOK_URL` | Same as Slack but to the configured destination | On leak |
| **Alertmanager webhook token** | `ALERTMANAGER_WEBHOOK_TOKEN` | Can publish synthetic alerts that the agent will react to | On leak; rotate quarterly |

## API bearer token rotation

**Blast radius if leaked:** an attacker can list all incidents, see the
diagnoses + audit trail, flip the agent mode, approve queued actions,
trigger use cases. They cannot directly bypass OPA (the agent's own
policy gate still runs); but Approve flips ARE allowed since they're
intentional human-override.

```bash
# 1. Generate the new token.
NEW_TOKEN=$(openssl rand -base64 32)

# 2. Update the K8s Secret. The api pod reads via SHP_API_TOKEN_FILE
#    (projected from this Secret), so kubelet refreshes the on-disk
#    file within ~60s — no pod restart needed.
kubectl -n pocteo create secret generic pocteo-pocteo-auth \
  --from-literal=SHP_API_TOKEN="$NEW_TOKEN" \
  --from-literal=AUDIT_HMAC_KEY="$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.AUDIT_HMAC_KEY}' | base64 -d)" \
  --dry-run=client -o yaml \
  | kubectl -n pocteo apply -f -

# 3. (Laptop / docker-compose path)
sed -i.bak "s|^SHP_API_TOKEN=.*|SHP_API_TOKEN=$NEW_TOKEN|" .env.local
docker compose restart api

# 4. Update the SPA's stored token. Each operator pastes the new value
#    into the SPA's first-load prompt OR via the localStorage key
#    `shp_token`. There's no central revocation list; the old token is
#    simply not accepted after the rotation completes.

# 5. Verify.
curl -sS -H "Authorization: Bearer $NEW_TOKEN" http://localhost/api/health
# Expect 200.
curl -sS -H "Authorization: Bearer <OLD_TOKEN>" http://localhost/api/health
# Expect 401.
```

The api process uses `secrets.compare_digest` so token comparison is
constant-time — the old token doesn't leak via timing.

## Audit HMAC key rotation

**Blast radius if leaked:** attacker can edit the audit log AND re-sign
their edits — the chain still verifies. The audit log's forensic
guarantee depends entirely on this key. Treat the key like a signing
key for a CA.

**Critical caveat:** Rotating the key INVALIDATES the chain for all
audit lines signed with the previous key. There is no "re-sign with the
new key" mode; that would defeat the point. Two viable patterns:

### Pattern 1 — Keep old keys on disk for verification

```bash
# Save the current key alongside the next-key rotation.
OLD_KEY=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.AUDIT_HMAC_KEY}' | base64 -d)
NEW_KEY=$(openssl rand -base64 32)

# Capture the rotation timestamp + old key into a separate
# verification-only secret. This lets `verify_audit_log` re-verify
# pre-rotation lines with the old key.
kubectl -n pocteo create secret generic pocteo-pocteo-audit-keys \
  --from-literal="key-2026-06-01=$OLD_KEY" \
  --from-literal="key-current=$NEW_KEY" \
  --dry-run=client -o yaml \
  | kubectl -n pocteo apply -f -

# Update the active secret with the new key.
kubectl -n pocteo get secret pocteo-pocteo-auth -o yaml \
  | sed "s|AUDIT_HMAC_KEY:.*|AUDIT_HMAC_KEY: $(echo -n "$NEW_KEY" | base64 -w0)|" \
  | kubectl apply -f -

# Restart api to pick up the new key.
kubectl -n pocteo rollout restart deploy/api
```

When verifying old lines, point `verify_audit_log` at the right key:

```python
from agent.audit import verify_audit_log
# pre-rotation:
n, bad = verify_audit_log("/audit/audit.log.2026-05-31", OLD_KEY)
# post-rotation:
n, bad = verify_audit_log("/audit/audit.log", NEW_KEY)
```

### Pattern 2 — Snapshot + reset

Acceptable when you're rotating because of a leak and want the audit
chain reset:

```bash
# 1. Take a final snapshot of the old chain with proof of integrity.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo exec deploy/api -- python -c "
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f'pre-rotation: {n} records, {len(bad)} invalid')
" > "audit-chain-final-${TS}.txt"

kubectl -n pocteo exec deploy/api -- \
  tar czf - -C / audit > "audit-archive-${TS}.tar.gz"
sha256sum "audit-archive-${TS}.tar.gz" > "audit-archive-${TS}.sha256"

# 2. Rotate the key.
NEW_KEY=$(openssl rand -base64 32)
kubectl -n pocteo create secret generic pocteo-pocteo-auth \
  --from-literal=SHP_API_TOKEN="$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)" \
  --from-literal=AUDIT_HMAC_KEY="$NEW_KEY" \
  --dry-run=client -o yaml \
  | kubectl -n pocteo apply -f -

# 3. Start a fresh chain. Truncate the active log (rotated siblings stay
#    as historical artifacts).
kubectl -n pocteo exec deploy/api -- truncate -s 0 /audit/audit.log

# 4. Restart api.
kubectl -n pocteo rollout restart deploy/api

# 5. Document the rotation in your incident log + store the snapshot tar.
```

## Slack / Generic webhook rotation

Webhooks are read from env at process start time, so a rotation requires
a pod bounce. Both are optional — if you don't set them, no problem.

```bash
# 1. Generate the new webhook (Slack admin → app → Incoming Webhooks).
NEW_HOOK="https://hooks.slack.com/services/T0/B0/NEW"

# 2. Update via Helm.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set-string notifications.slackWebhookUrl="$NEW_HOOK"

# 3. Verify with a synthetic escalation. Trigger lib-drain-low-severity
#    which always escalates.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-drain-low-severity/run

# 4. Watch the audit log for `page_delivery` outcome:
kubectl -n pocteo exec deploy/api -- grep '"page_delivery"' /audit/audit.log | tail -1
# Expect: a delivery record with `channel: "slack", ok: true`.

# 5. Revoke the old webhook in Slack admin.
```

## Alertmanager webhook token rotation

```bash
# 1. Generate.
NEW_TOK=$(openssl rand -hex 32)

# 2. Update Keos.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set-string inbound.alertmanagerWebhookToken="$NEW_TOK"

# 3. Update Alertmanager.
# Edit your Alertmanager config (`http_config.authorization.credentials`)
# to use the new token. Apply with whichever method you use
# (alertmanager-secret, kustomize, helm, etc.).

# 4. Verify by sending a synthetic alert.
curl -sS -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $NEW_TOK" \
  http://localhost/api/webhooks/alertmanager \
  -d '{"version":"4","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"RotationTest"},"annotations":{"summary":"rotation test"},"fingerprint":"rot-test"}]}'
# Expect 200, `accepted: 1`.

# 5. Verify the old token is rejected.
curl -sS -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer OLD_TOK" \
  http://localhost/api/webhooks/alertmanager -d '{"alerts":[]}'
# Expect 503 (fail-closed when the token doesn't match — the agent's
# webhook auth returns 503 for both unset and rejected, on purpose).
```

## After any rotation

```bash
# Verify the agent still works end-to-end.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor

# Record the rotation in your secret-tracking system (Vault audit log,
# 1Password rotation log, etc.) with:
#   - Date of rotation
#   - Reason (scheduled / leak suspicion / operator turnover)
#   - Who performed the rotation
#   - Next scheduled rotation
```

## Going forward — operational improvements

- **Don't put rotation in a calendar.** Add a Prometheus alert on
  secret age (`kube-state-metrics` exposes `kube_secret_created`) so
  rotation cadence is observable.
- **External secret stores** (Vault, AWS Secrets Manager, GCP Secret
  Manager) integrated via External Secrets Operator make rotation
  zero-touch — the Operator pushes the new value, kubelet projects it
  on disk, the api re-reads. See your secrets-management runbook.
- **Per-operator JWTs** instead of a shared bearer token. See
  [SECURITY.md §Hardening checklist](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md) item 7 + the
  "Going forward" section of [incident-response-agent.md](incident-response-agent.md).

## Postmortem template

Only needed if rotation was triggered by a leak. Same shape as
[audit-tampering.md §Postmortem template](audit-tampering.md#postmortem-template).
