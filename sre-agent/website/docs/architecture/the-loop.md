---
id: the-loop
slug: /architecture/the-loop
title: The loop — Observe → Predict → Heal → Learn
sidebar_position: 2
description: Node-by-node breakdown of the LangGraph state machine.
---

# The loop — node by node

The Keos decision loop is a LangGraph `StateGraph` with 8 nodes + 1
terminal escalate node. State flows through a single `IncidentState`
`TypedDict`; every node either returns `{...partial update...}` or raises.

## State shape

```python
class IncidentState(TypedDict, total=False):
    incident_id: str
    raw_event: dict
    severity: Literal["low", "medium", "high", "critical"]
    namespace: str | None
    pod: str | None
    rule: str
    agent_mode: Literal["observe-only", "active", "frozen"]
    context: dict                  # runbook_hits + pod_describe + top_slug
    k8sgpt_findings: list[dict]
    diagnosis: dict                # summary, root_cause, confidence, evidence
    proposed_action: dict          # tool, args, scope, reversible
    policy_decision: dict          # allow, reasons
    action_result: dict            # tool, args, success, stdout, stderr
    verification: dict             # healed, checks, notes
    escalated: bool
    stale_resolved: bool
```

State is purely additive — each node merges new fields into the dict.
No node mutates fields written by a previous one.

## Phase 1: Observe

### `triage`

**Input:** the raw NATS event.
**Output:** `{severity, namespace, pod, rule, agent_mode, stale_resolved?}`

Validates the event and short-circuits stale ones (event >10 min old
AND pod is healthy now OR pod is gone). Stale-resolved incidents skip
the LLM cycle entirely — they go straight to `escalate` with a clean
"already healed" reason and no Slack page.

The agent mode is captured here so a mid-graph mode flip
(`kubectl patch agentmode`) doesn't change behavior mid-incident.

### `retrieve_context`

**Input:** rule + output_fields.
**Output:** `state.context = {runbook_hits, pod_describe, top_slug}`

Two parallel reads:

1. **RAG** via Qdrant. Embed `rule + output` with `nomic-embed-text`
   (768 dim, COSINE), query the `runbooks` collection for top-3.
2. **Live state** via `kubectl describe pod <pod>` — captures restart
   count, last termination reason, events.

On Qdrant failure: graceful degrade. Returns empty `runbook_hits` and
the graph continues with just K8sGPT findings + pod describe as LLM
context. The model is more likely to escalate defensively, which is
the right behavior when we lack runbook context.

### `k8sgpt_analyze`

**Input:** namespace.
**Output:** `state.k8sgpt_findings = list[Finding]`

Calls the K8sGPT MCP gateway with namespace + pod filters. K8sGPT's
analyzers are deterministic rule-based code (no LLM) that look for
known failure patterns: ImagePullBackOff, CrashLoopBackOff, OOMKilled,
PVC-Pending, etc. Findings are filtered to those that mention the
target pod or its owner.

On K8sGPT unavailable: graceful degrade. Empty findings list.

## Phase 2: Predict

### `diagnose`

**Input:** event + context + findings.
**Output:** `state.diagnosis = {summary, root_cause, confidence, evidence, notes}`

The first LLM call. The prompt assembles:

- The raw event (rule + output_fields)
- The top-3 runbook hits (`text` + `score`)
- K8sGPT findings (if non-empty)
- The pod describe output

Constrained to JSON output (`format: "json"`, `temperature: 0.1`,
`num_predict: 512`). Schema-validated with Pydantic. Retries once on
validation failure with a `your JSON was invalid, return only the
object matching schema X` follow-up. Escalates on the second failure.

Routing: `confidence < 0.6` → escalate (low-confidence diagnosis is
the LLM saying "I don't actually know what's wrong"; better to page
a human than guess).

### `plan_action`

**Input:** state.
**Output:** `state.proposed_action = {tool, args, scope, reversible}`

A second LLM call. Prompt is the diagnosis + the runbook hits + the
constrained tool catalog:

```
restart_pod      | scope=pod    | reversible=true
rollout_undo     | scope=deploy | reversible=true
scale_deployment | scope=deploy | reversible=true
cordon_node      | scope=node   | reversible=true
drain_node       | scope=node   | reversible=true
page_human       | scope=n/a    | reversible=true
```

Same JSON-output + retry pattern as `diagnose`. The model is
explicitly told to pick `page_human` when in doubt.

## Phase 3: Heal

### `policy_gate`

**Input:** proposed_action + severity + agent_mode + target.namespace.
**Output:** `state.policy_decision = {allow, reasons}`

POSTs to OPA's `/v1/data/agent/authz` endpoint:

```json
{
  "input": {
    "action": "restart_pod",
    "severity": "high",
    "agent_mode": "active",
    "target": {"namespace": "workloads", "kind": "Pod", "name": "foo"}
  }
}
```

OPA evaluates `policies/agent.rego`. The matrix maps each
(action, severity) pair to a level L1-L4; `allow` if `level <= 2`.
Hard-deny rules fire on:

- protected namespaces (`kube-system`, `kube-public`)
- agent_mode != "active"
- unknown actions

On OPA unreachable: fail-closed. `allow=false` with reason
`OPA unreachable — fail-closed default`. Routes to escalate.

### `execute`

**Input:** proposed_action + agent_mode.
**Output:** `state.action_result = {tool, args, success, stdout, stderr, dry_run?}`

Two paths:

- `agent_mode == "observe-only"` → dry-run. Log the would-be action
  with `dry_run: true`. No kubectl call.
- `agent_mode == "active"` → call the MCP server. The MCP server
  runs OPA *again* (defense-in-depth, same matrix), then invokes the
  tool with a scoped ServiceAccount kubeconfig.

The MCP roundtrip + actual kubectl call typically completes in
50-500 ms (no LLM involved).

## Phase 4: Learn

### `verify`

**Input:** action_result + target.
**Output:** `state.verification = {healed: bool|null, checks, notes}`

Action-class dispatch:

- `restart_pod` / `rollout_undo` / `scale_deployment` → poll
  `kubectl get pod -l selector` for up to 30 s, waiting for
  expected ready replicas. `healed = (ready_count == desired_count)`.
- `cordon_node` / `drain_node` → kubectl-success IS the verification.
  `healed = true`.
- `page_human` → cluster state unchanged; `healed = null`,
  `escalated = true`. The chip shows "Escalated" not "Healed".
- dry-run (mode=observe-only) → `healed = true, notes = "dry-run mode"`.

### `escalate`

**Input:** state.
**Output:** writes audit; calls `tools.page_human` (if not stale-resolved);
sets `state.escalated = true`.

Terminal node. Called when any earlier guard routes here:

- triage stale-resolved (no page — pod healed itself)
- low-confidence diagnose
- OPA deny
- execute failure
- verify-not-healed

The audit record captures the reason; Slack/PagerDuty webhooks get
the full context.

## Routing recap

| From | Condition | To |
|---|---|---|
| triage | `stale_resolved=true` | escalate |
| triage | otherwise | retrieve_context |
| diagnose | `confidence < 0.6` | escalate |
| policy_gate | `allow=false` | escalate |
| execute | error | escalate |
| verify | `healed=true` AND not escalated | END |
| verify | `escalated=true` (page_human) | END |
| verify | otherwise | escalate |

The escalate node always ends the graph; there's no recovery loop.
This is intentional — a second LLM cycle on the same failed event
just burns CPU.

## Instrumentation

Each node is wrapped in `pipeline_trace.instrumented(name, fn)`:

```python
async def instrumented(name, fn, state):
    audit.emit(state["incident_id"], f"{name}.start", {"input_snapshot": ...})
    trace_bus.publish(state["incident_id"], {"node": name, "phase": "start", ...})
    try:
        result = await fn(state)
        audit.emit(state["incident_id"], f"{name}.end", {"output_snapshot": ..., "elapsed_ms": ...})
        trace_bus.publish(state["incident_id"], {"node": name, "phase": "end", ...})
        return result
    except Exception as exc:
        audit.emit(state["incident_id"], f"{name}.end", {"error": str(exc), ...})
        trace_bus.publish(state["incident_id"], {"node": name, "phase": "end", "error": ...})
        raise
```

The trace bus is in-memory pub/sub keyed by `incident_id`. WebSocket
clients subscribe to `pipeline.<incident_id>` and get every event in
real time. Trace records also append to `data/traces/<incident_id>.jsonl`
so the SPA can replay completed incidents.

## Where the magic isn't

The LangGraph has no recursion. No tool loops. No multi-step
reasoning where the LLM gets to act, observe, and act again. Every
incident is one diagnose, one plan, one execute, one verify. If that's
not enough, we escalate. **This is the whole product.** The
constraint is the safety property.
