---
id: audit-chain
slug: /architecture/audit-chain
title: Audit chain
sidebar_position: 4
description: How the HMAC-signed audit chain works and how to verify it offline.
---

# Audit chain

Every state transition the agent makes is captured in a tamper-evident,
HMAC-chained log. This page covers the format, the chaining formula,
and the offline verifier.

## Why a custom chain

K8s API audit logs cover API server calls. We want one chain that
captures:

- The LLM call's prompt + response
- The policy gate's input + decision
- The kubectl call's stdout/stderr/exit
- The verify node's outcome
- Any escalation reason
- Operator overrides (mode flips, approvals)

A standard Kubernetes audit log captures only the last bullet. We need
all six. So Keos writes its own log, with its own integrity guarantee.

## Format

One JSON object per line. Newline-delimited; no leading/trailing
whitespace. UTF-8.

```json
{
  "ts": "2026-06-01T21:03:12.405Z",
  "incident_id": "uc-5596580194",
  "event_type": "execute.end",
  "payload": {
    "tool": "restart_pod",
    "args": {"namespace": "workloads", "pod_name": "nginx-demo-..."},
    "action_result": {
      "tool": "restart_pod",
      "success": true,
      "stdout": "pod \"nginx-demo-...\" deleted\n",
      "stderr": "",
      "exit_code": 0
    },
    "elapsed_ms": 187
  },
  "hmac": "8f4a3e7c..."
}
```

Required fields: `ts`, `incident_id`, `event_type`, `payload`, `hmac`.

## Chaining formula

```python
canon_i = canonical_json({
    "ts": record_i.ts,
    "incident_id": record_i.incident_id,
    "event_type": record_i.event_type,
    "payload": record_i.payload,
})
hmac_i = HMAC-SHA256(
    key   = AUDIT_HMAC_KEY,
    data  = prev_hmac || canon_i,
)
```

Where:

- `canonical_json` is deterministic JSON serialization (sorted keys,
  no whitespace). Two different Python processes serializing the same
  dict will produce byte-identical output.
- `prev_hmac` is the HMAC of line `i-1`. For the first line in the
  file, `prev_hmac = ""` (empty string).
- `||` denotes byte concatenation.

The chain is **linear and forward-only**. An attacker who edits line N
must re-sign line N (needs the key), line N+1, line N+2, … through to
the end of the file (still needs the key). Without the key, no edit
is recoverable.

## Rotation

The audit log uses `TimedRotatingFileHandler` with `when='midnight'`:

```
agent/audit.log               <- today
agent/audit.log.2026-05-31    <- yesterday
agent/audit.log.2026-05-30
```

The chain **does not** carry across rotation. Each daily file is its
own chain starting from `prev_hmac = ""`. This means:

- Verifying a single day's file is independent.
- Tampering with yesterday's file doesn't break today's.
- Backing up the rotated siblings (`agent/audit.log.YYYY-MM-DD`) is
  sufficient to preserve forensic continuity for that day.

Retention is `AUDIT_LOG_RETENTION_DAYS` (default 30 days). Older
files are pruned by the background retention loop in `serve.py`.

## Offline verification

The verifier is in `agent.audit.verify_audit_log()`:

```python
from agent.audit import verify_audit_log
from agent.config import settings

n, bad = verify_audit_log(
    settings.audit_log_path,
    settings.audit_hmac_key,
)
print(f"{n} records, {len(bad)} invalid")
```

Walks all sibling files matching `audit.log*`, computes each line's
expected HMAC, compares against the recorded HMAC. `bad` is a list of
`(file_path, line_number, reason)` tuples for any line that didn't
verify.

For a clean chain, output is `N records, 0 invalid`.

## When verification fails

`len(bad) > 0` is a forensic event. Reasons in priority order:

1. **The key was rotated** without preserving the old key for
   verification. Pre-rotation lines are unverifiable. See the
   [secret-rotation runbook](../runbooks/secret-rotation) for the
   "keep old keys for verification" pattern.
2. **The file was edited** by an operator (well-intentioned or not).
   This is the case the chain is designed to detect.
3. **The file was truncated mid-write** because of a disk-full event.
   Verifier reports the first broken line; lines after it are also
   broken because they no longer chain back to a valid prev_hmac.
4. **The `prev_hmac` lineage was disrupted** by an explicit reset
   (e.g., the file was deleted and recreated). All lines after the
   reset look "valid" to a chain that starts with `""` — but the
   continuity claim is broken.

Treat any nonzero `bad` as a P1 forensic investigation. The
[audit-tampering runbook](../runbooks/audit-tampering) walks through
the response.

## What's audited, what isn't

**Audited:**

- Every node entry + exit (with input/output snapshots)
- Every LLM prompt + response (hashed payload to avoid PII leakage in
  raw form — full prompts in the trace file, hash in the audit chain)
- Every policy_gate decision (input + allow/deny + reasons)
- Every kubectl call (tool, args, stdout, stderr, exit code)
- Every verify outcome
- Every escalate reason
- Every operator approval / mode flip (via `human_override=true` field)
- Every webhook delivery (Slack / PagerDuty / Alertmanager)

**Not audited:**

- Read-only operations the agent does to gather context
  (`kubectl describe pod`, Qdrant queries, K8sGPT analyzes). These
  don't affect cluster state and are reproducible from `pipeline_trace`.
- The SPA's GET requests against `/api/incidents` etc. Authorization
  is enforced at the API layer; access patterns aren't forensic.

## Shipping to SIEM

The audit log is one JSON-per-line file. Loki, Datadog, Splunk, and
ELK all consume that format natively. Configure your log shipper to:

- Tail `agent/audit.log` AND all rotated siblings (`audit.log.*`)
- Parse each line as JSON
- Preserve the `hmac` field (don't strip it during transformation —
  the consumer needs it to re-verify)

For an air-gapped environment, ship the entire `audit/` directory
nightly via your standard backup pipeline (Velero, restic, rsync).
See the [backup runbook](../runbooks/disaster-recovery) for the
restore procedure.
