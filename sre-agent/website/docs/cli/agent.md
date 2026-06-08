---
id: agent
slug: /cli/agent
title: Agent commands
sidebar_position: 4
description: keosctl events · run · usecases · audit — interact with the agent's incidents, use cases, and HMAC-signed audit chain.
---

# Agent commands

These commands talk to the FastAPI agent itself — same endpoints the React SPA uses. They're the easiest way to script the agent or wire it into other tooling.

Under the hood, every command in this group reaches the API by `kubectl exec`'ing into the `api` pod and curling `http://localhost:8000` with the cached bearer token. No external port-forward needed.

## `keosctl events` — recent incidents

Lists the most recent incidents the agent has processed, colour-coded by outcome:

```bash
$ keosctl events

Recent incidents
────────────────────────────────────────────────────────────────
  ●  2026-06-09T08:42:14   pod.oom_killed/workloads      restart_pod         healed
  ●  2026-06-09T08:40:01   pod.crashloop/workloads       rollout_undo        escalated
  ●  2026-06-09T08:35:55   node.disk_pressure            cordon_node         healed
  ●  2026-06-09T08:30:12   pod.image_pull_back_off       page_human          escalated
```

Colour code:

| Colour | Outcome |
|---|---|
| 🟢 green | `healed` — the agent ran a remediation and `verify` confirmed the cluster recovered |
| 🟡 yellow | `escalated` — the agent paged human via `page_human` (matrix denied, low confidence, or `verify` failed) |
| 🔴 red | `failed` — the action errored before `verify` |
| ⚪ dim | other (`stale_resolved`, in-flight, unknown) |

Flags:
- `-n N`, `--limit N` — number of incidents (default 10)
- `-w`, `--watch` — refresh every 3 s, live stream

Empty state when nothing has happened yet:

```text
Recent incidents
────────────────────────────────────────────────────────────────
  No incidents yet — try "keosctl run lib-memory-leak".
```

## `keosctl run <use-case-slug>` — trigger a use case

Fires a use case end-to-end, exactly as the SPA's "Run" button does:

```bash
$ keosctl run lib-memory-leak

Running use case 'lib-memory-leak'
────────────────────────────────────────────────────────────────
  ✓ Incident id: inc_2026-06-09T08-42-14_a1b2c3d4
  Watch live: keosctl events -w
  Open SPA:   keosctl ui  (Pipeline tab)
```

What happens under the hood:
1. `POST /api/usecases/<slug>/run` — the agent's `usecase_runner` synthesises a Falco-shape event matching the use case definition
2. The event is published to NATS on `infra.events.*`
3. The 9-node LangGraph picks it up; the Pipeline view in the SPA animates the run in real time
4. (Optional) If the use case includes a Chaos Mesh experiment, it's applied to a target pod for realism

For a list of available use cases:

```bash
keosctl usecases list
```

## `keosctl usecases` — list and inspect use cases

```bash
$ keosctl usecases list

Available use cases
────────────────────────────────────────────────────────────────
  lib-memory-leak              Memory leak in workloads namespace
  lib-crashloop-recovery       Pod crashlooping due to bad config
  lib-node-pressure            Node disk pressure → eviction
  custom-ml-model-oom          (operator-authored) ML model OOM scenario
```

Show one in detail:

```bash
$ keosctl usecases show lib-memory-leak
{
  "slug": "lib-memory-leak",
  "title_en": "Memory leak in workloads namespace",
  "title_fr": "Fuite mémoire dans le namespace workloads",
  "severity": "high",
  "namespace": "workloads",
  "synthetic_event": { … },
  "expected_outcome": { "tool": "restart_pod", "verified_healed": true },
  …
}
```

## `keosctl audit` — the HMAC-signed audit chain

Every state transition the agent makes is logged with an HMAC-SHA256 chain that links each record to the previous one. Tamper-evident: a single edit breaks the chain.

### `audit tail` — recent entries

```bash
keosctl audit tail                # last 20 lines
keosctl audit tail -n 100         # last 100 lines
```

The audit log is JSON-lines, one record per state transition:

```json
{
  "ts": "2026-06-09T08:42:14.123",
  "incident_id": "inc_2026-06-09T08-42-14_a1b2c3d4",
  "event_type": "execute",
  "payload": { "tool": "restart_pod", "target_pod": "memory-leak-abc-123", … },
  "prev_hmac": "f8a3…",
  "hmac": "9b2c…"
}
```

### `audit verify` — chain integrity check

```bash
$ keosctl audit verify
  ✓ Verified 1,247 records — chain intact

$ keosctl audit verify              # if tampered
  ✗ Chain break at record 832
    prev_hmac mismatch — record was modified or inserted out of order
```

Runs the same verifier the FastAPI exposes at `/api/audit/verify`. On a clean chain, exit code 0; on any break, exit code 1 with the offending record number.

Use this as a regular cron job for tamper detection:

```bash
0 */6 * * * keosctl audit verify >> /var/log/keos-audit-verify.log 2>&1
```

## Common workflows

### "Show me everything that happened in the last hour, on a chart"

```bash
# Quick screen-pasted dashboard
watch -n 5 'keosctl events -n 30'
```

Or use the SPA's Incidents view via `keosctl ui` for filters + drill-down.

### "Demo: trigger a memory leak + watch the agent fix it"

```bash
keosctl run lib-memory-leak
keosctl events -w                  # in a second terminal
```

On the recommended Jetson Orin GPU, the run completes in ~30 s end-to-end. CPU-only on a laptop demo runs in 3-5 min.

### "Tamper-detect script for forensics"

```bash
keosctl audit verify || curl -X POST <incident-management-system> \
  -H 'Content-Type: application/json' \
  -d '{"severity":"critical","summary":"Keos audit chain break"}'
```

## See also

- [Operations](./operations) — `status`, `doctor`, `logs`, `ui`, `mode`
- [The agent loop](../architecture/the-loop.md) — what `events` is actually showing you
- [Safety model](../architecture/safety-model.md) — what the audit chain protects against
