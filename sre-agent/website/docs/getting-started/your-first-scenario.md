---
id: your-first-scenario
slug: /getting-started/your-first-scenario
title: Your first scenario, in depth
sidebar_position: 4
description: Walk through the lib-memory-leak use case node by node, and learn how to read the audit chain.
---

# Your first scenario, in depth

We'll walk through `lib-memory-leak` — the use case `make demo-ui` runs
by default. Each node in the LangGraph fires; we'll show what it reads,
what it writes, and where to find that in the audit log.

## The setup

The k3d cluster created by `make install` has three demo workloads in
the `workloads` namespace:

```bash
$ kubectl get pods -n workloads
NAME                            READY   STATUS             RESTARTS   AGE
broken-image-594b7fdbb6-mrprv   0/1     ImagePullBackOff   0          5m
busybox-bad-5b7b7796bb-xfls8    0/1     CrashLoopBackOff   3          5m
nginx-demo-7b8d5456d6-7shtv     1/1     Running            0          5m
nginx-demo-7b8d5456d6-p47q5     1/1     Running            0          5m
nginx-demo-7b8d5456d6-zlxmt     1/1     Running            0          5m
```

The `lib-memory-leak` use case targets one of the healthy `nginx-demo`
pods and simulates memory pressure via a synthetic event. The agent has
to decide what to do.

## Step 1: trigger

From the SPA's **Use Cases** view, find `lib-memory-leak` and click
**Run**. Behind the scenes:

1. `agent.usecase_runner` renders the use case's event template (substituting
   a real pod name in `{pod}`).
2. The event is published to NATS subject `infra.events.usecase.lib-memory-leak`.
3. The agent's `[user]` lane consumer picks it up and starts a new
   LangGraph run with a fresh `incident_id` (prefix `uc-`).

In the UI, your browser is redirected to `/pipeline/<incident_id>` —
the live timeline.

## Step 2: triage

The first node validates the event:

- Is the pod still in the cluster?
- Is the event older than `STALE_EVENT_S` (default 600 s)?
- Is the agent in `frozen` mode?

For a fresh use-case run, all three checks pass. Triage extracts
`{severity: "medium", namespace: "workloads", pod: "nginx-demo-…", rule:
"Container memory leak / OOM trajectory"}` and routes to
`retrieve_context`.

```json
// audit.log entry
{
  "ts": "2026-06-01T21:03:12.405Z",
  "incident_id": "uc-5596580194",
  "event_type": "triage",
  "payload": {
    "input": {"rule": "Container memory leak ...", ...},
    "result": {"severity": "medium", ...},
    "stale_resolved": false
  },
  "hmac": "8f4a3e..."
}
```

## Step 3: retrieve_context

Two parallel reads:

1. **RAG hit**: embed the rule+output, query Qdrant for the top-3 runbooks.
   For this scenario, the top hit is `usecase-lib-memory-leak`
   (the runbook the use case ships) with similarity ~0.79.
2. **Pod describe**: `kubectl describe pod <pod>` to enrich the LLM context
   with the live state.

Both go into `state.context`.

## Step 4: k8sgpt_analyze

K8sGPT runs its deterministic analyzers against the cluster — looking
for things like ImagePullBackOff, CrashLoopBackOff, OOMKilled. For a
healthy nginx pod with synthetic memory pressure, K8sGPT typically
finds nothing dramatic; the field stays empty.

## Step 5: diagnose (LLM call #1)

The first LLM call. The prompt is built from:

- The Falco-shaped event (rule + output + fields)
- Top-3 RAG runbook hits (`text` + `score`)
- K8sGPT findings (if any)
- The pod describe output

The LLM returns structured JSON:

```json
{
  "summary": "Container memory RSS rising at +18 MiB/min in nginx-demo-...",
  "root_cause": "memory leak in application code",
  "confidence": 0.82,
  "evidence": ["RAG runbook usecase-lib-memory-leak matched", "..."],
  "notes": ""
}
```

If `confidence < AGENT_CONFIDENCE_THRESHOLD` (default 0.6), the graph
short-circuits to escalate. For this scenario, confidence is 0.82, so
we proceed.

**This is the slowest node.** First-ever LLM call (model cold-load)
takes 5–6 minutes on CPU. Subsequent calls in the same install: ~30
seconds. On GPU (Jetson Orin Nano): ~2 seconds.

## Step 6: plan_action (LLM call #2)

A second LLM call. The prompt:

- The diagnosis from step 5
- The runbook hits (RAG hint at the right action)
- The constrained tool catalog: `restart_pod | rollout_undo | scale_deployment | cordon_node | drain_node | page_human`

Returns:

```json
{
  "tool": "restart_pod",
  "args": {"namespace": "workloads", "pod_name": "nginx-demo-..."},
  "scope": {"namespace": "workloads"},
  "reversible": true
}
```

## Step 7: policy_gate

OPA evaluates the proposed action against the matrix in
`policies/agent.rego`:

```
input.action      = "restart_pod"
input.severity    = "medium"
input.agent_mode  = "active"   (or "observe-only" — dry-run mode)
input.target.namespace = "workloads"
```

The matrix grants `restart_pod` at `medium` severity (Level L1 — always
allowed in non-protected namespaces). OPA returns `allow=true`. If
the mode were `observe-only`, the policy still returns true but
`execute` would dry-run the call.

## Step 8: execute

The agent calls the MCP server (`http://localhost:8090/mcp`) with the
proposed action. The MCP server **also calls OPA** (defense in depth)
before invoking `kubectl delete pod nginx-demo-…`. The kubelet sees
the deletion + the ReplicaSet creates a fresh replica.

`action_result` captures the kubectl stdout/stderr + exit code.

## Step 9: verify

The agent polls `kubectl get pod -l <selector>` for up to 30 seconds,
waiting for at least 3 ready replicas (the original Deployment spec).

For a successful restart, this returns within 5–10 seconds with
`healed=true`. Verify ends the graph.

```json
{
  "ts": "...",
  "incident_id": "uc-5596580194",
  "event_type": "verify",
  "payload": {"healed": true, "notes": "...", "checks": [...]}
}
```

## Step 10: graph done

`serve.py` calls `save_incident(...)` to persist the full
`IncidentState` JSON to `data/incidents/<id>.json`. The pipeline trace
already lives in `data/traces/<id>.jsonl`. Both are kept for the
configured retention window (default 90 / 30 days).

The **Incidents** view in the SPA now shows the completed incident
with `Outcome=Healed` and a green chip.

## Reading the audit chain

Every event_type above writes one HMAC-signed JSON line to
`agent/audit.log`. Each line includes:

- `ts` — UTC ISO timestamp
- `incident_id` — links it to the incident
- `event_type` — `triage | retrieve_context | k8sgpt_analyze | diagnose | plan_action | policy_gate | execute | verify | escalate | page_delivery`
- `payload` — node-specific data
- `hmac` — HMAC-SHA256 over `(prev_hmac, ts, incident_id, event_type, canonical_json(payload))` using `AUDIT_HMAC_KEY`

Verify the chain offline:

```python
from agent.audit import verify_audit_log
from agent.config import settings
n, bad = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f"{n} records, {len(bad)} tampered")
```

Expected: `N records, 0 tampered`. Any non-zero `bad` means the chain
was modified after the fact — see [audit-tampering runbook](../runbooks/audit-tampering).

## What's next

- Try a different scenario: `lib-crashloop-recovery`, `lib-node-pressure`,
  `lib-drain-low-severity`.
- Write your own use case via the SPA's **Use Case Creator**.
- Read [Architecture → Overview](../architecture/overview) for the
  Mermaid diagrams of every component.
- When something breaks: [Runbooks](../runbooks/).
