---
id: runaway-graph
slug: /runbooks/runaway-graph
title: Runaway graph
sidebar_position: 7
---

# Runbook: runaway graph (same incident escalating repeatedly)

## Symptom

- The Incidents view shows the same pod / namespace / rule re-escalating
  every few minutes for 30+ minutes.
- `keos_escalations_total{reason="matrix_denial"}` or `="execute_failed"`
  rate is high and not dropping.
- Operator gets paged repeatedly for the same underlying issue.
- The pending-approvals queue keeps growing with the same proposed action.

## Triage (5 min)

```bash
# 1. How many recent incidents for the same target?
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/incidents | \
  jq -r '.items[:20] | .[] | "\(.ts) \(.namespace)/\(.pod) -> \(.proposed_tool) [\(.escalated|tostring)]"' \
  | sort | uniq -c | sort -rn | head -10

# 2. Look at one recent one to see WHY it escalated.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost/api/incidents/<id-from-step-1>" | \
  jq '{escalated, escalation_reason, policy_decision, action_result, verification}'

# 3. Is the cluster genuinely broken, or has it already healed?
kubectl describe pod <pod-from-step-1> -n <namespace-from-step-1> | tail -30
```

| Triage result | Cause | Branch |
|---|---|---|
| Pod is healthy now, events from 20+ min ago | Stale-event filter not catching it (`STALE_EVENT_S` too high) | Resolution §A |
| `policy_decision.reasons` shows L3/L4 matrix denial | OPA matrix is correctly denying; agent + LLM are doing the right thing, runbook is misguiding it | Resolution §B |
| `action_result.success: false` repeatedly with the same `error` | kubectl call fails deterministically — see the error | Resolution §C |
| `verification.healed: false`, action succeeded | The action didn't fix the symptom (e.g. cordon_node on the wrong node) | Resolution §D |
| Pod genuinely keeps failing, action genuinely doesn't help | Real platform problem the agent can't fix from its tool catalog | Resolution §E |

## Containment

The single most-effective stop-the-bleeding move:

```bash
# Flip mode to observe-only. The graph still runs (so you keep getting
# audit + diagnoses for forensics) but execute is blocked.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"observe-only","note":"runbook: runaway-graph"}}'
```

If Slack is being spammed, also drop the webhook for the duration:

```bash
kubectl -n pocteo set env deploy/api SLACK_WEBHOOK_URL-
kubectl -n pocteo rollout status deploy/api
# Restore via `helm upgrade --reuse-values --set notifications.slackWebhookUrl=…` later.
```

## Resolution

### §A Stale events leaking through

Default `STALE_EVENT_S=600` (10 min). If your CPU-LLM backlog is longer
than that, by the time the graph reaches triage the event is genuinely
stale but the underlying pod state still looks "Ready=False" for other
reasons.

```bash
# Drop to 300 (5 min). New events get the tighter window.
kubectl -n pocteo set env deploy/api STALE_EVENT_S=300
kubectl -n pocteo rollout status deploy/api

# Purge the existing backlog (otherwise it'll grind through under the
# new rules anyway).
kubectl -n pocteo exec deploy/api -- python3 -c "
import asyncio, nats
async def main():
    nc = await nats.connect('nats://nats:4222')
    js = nc.jetstream()
    await js.purge_stream('INFRA')
    await nc.drain()
asyncio.run(main())
"
```

### §B Runbook is misguiding the LLM

This is the most operator-actionable case. The LLM picks the wrong tool
because the RAG hit it got is misleading.

```bash
# Identify which runbook the LLM grabbed.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost/api/incidents/<id>" | jq '.runbook_hits[0]'

# Edit the offending runbook source in your checkout.
# E.g. if runbook 004-node-pressure.md is suggesting drain_node when
# uncordon_node would be correct, fix the wording.

# Re-seed.
kubectl -n pocteo cp agent/runbooks pocteo/$(kubectl -n pocteo get pod -l app.kubernetes.io/component=api -o name | head -1 | sed 's|pod/||'):/app/agent/runbooks
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag
```

Or use the Use Case Creator (SPA) to author an inline runbook for the
specific scenario — that's faster than editing files when you only need
to teach the agent one new pattern.

### §C kubectl call fails deterministically

```bash
# Get the exact stderr.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost/api/incidents/<id>" | jq -r '.action_result.stderr'

# Common case: ServiceAccount RBAC is missing a verb for the tool.
# E.g. `Error from server (Forbidden): nodes "x" is forbidden: User
# "system:serviceaccount:pocteo:pocteo-agent" cannot patch resource
# "nodes" in API group ""`.

# Verify what we can do.
kubectl auth can-i --as=system:serviceaccount:pocteo:pocteo-agent --list

# Patch the ClusterRole.
kubectl edit clusterrole pocteo-agent
# Add the missing verb. Then verify the chart matches:
helm template pocteo deploy/helm/pocteo --reuse-values \
  | yq 'select(.kind == "ClusterRole" and .metadata.name == "pocteo-agent")'
```

### §D Action succeeded, verification failed

The classic case: the LLM proposed `restart_pod` but the underlying
problem is at the Deployment level, so deleting one pod just gets
another sick one back from the ReplicaSet.

This is a diagnostic-quality problem. Fix the runbook (§B) so future
incidents pick `rollout_undo` or `scale_deployment` instead.

### §E Real platform problem the catalog can't fix

Examples:
- Memory pressure on the node + no other node to drain to.
- ImagePullBackOff from a registry that's genuinely down.
- Storage class out of provisionable space.

The agent does the right thing: escalates. Your job here is to fix the
underlying platform issue (provision more nodes / fix the registry /
add storage), not to silence the agent. While you're working on the
platform issue, leave the agent in observe-only.

## After the fix

```bash
# Trigger a fresh use case to verify the corrected behavior.
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run

# Re-enable Slack if you muted it.
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set-string notifications.slackWebhookUrl=https://hooks.slack.com/...

# Mode back to active.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: runaway-graph cleared"}}'
```

## Postmortem template

Same shape as [agent-api-down.md](agent-api-down.md#postmortem-template).
Specific to runaway graphs, include:

- **What the LLM proposed and why.** Paste the diagnose JSON + plan JSON.
- **What the operator-correct action would have been.** This is the
  delta the runbook fix encodes.
- **Whether incident dedup would have changed the outcome.** A future
  work item: only process one incident per (ns, pod, root_cause) per
  N minutes. Mentioned in the original Phase 9 plan; deferred.
