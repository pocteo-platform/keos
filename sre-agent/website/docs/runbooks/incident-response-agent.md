---
id: incident-response-agent
slug: /runbooks/incident-response-agent
title: The agent IS the incident
sidebar_position: 10
---

# Runbook: when the agent itself is the incident

## Severity

**Critical.** The agent has cluster-mutating permissions. If it took an
action you didn't expect, treat it like any other authorized-but-misbehaving
service account.

## Symptom

- A pod / deployment / node is in an unexpected state and the audit log
  shows the agent did it.
- A drain went through that operator policy says shouldn't have.
- The Approvals queue shows an action `human_override=true` that you
  didn't approve.
- A `helm uninstall` happened — check the audit chain for the source.

## Triage (5 min)

**Stop the bleeding FIRST, investigate SECOND.**

```bash
# 1. Flip to frozen IMMEDIATELY. OPA denies every action.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"frozen","note":"runbook: incident-response-agent — investigating"}}'

# 2. Capture the audit log for forensics BEFORE anything else.
TS=$(date -u +%Y%m%dT%H%M%SZ)
kubectl -n pocteo exec deploy/api -- \
  tar czf - -C / audit > "agent-incident-${TS}.tar.gz"
sha256sum "agent-incident-${TS}.tar.gz" > "agent-incident-${TS}.sha256"

# 3. Identify the surprising actions.
kubectl -n pocteo exec deploy/api -- \
  grep -E 'execute|escalate' /audit/audit.log \
  | tail -50 \
  | jq -r '"\(.ts) \(.event_type) \(.payload.tool // .payload.action_result.tool // "?") \(.payload.action_result.success // "?")"' \
  | sort -r | head -20
```

## Containment

The kill-switch (`mode: frozen`) you flipped in step 1 stops the agent's
write path at the OPA gate. The agent process is still running; it's
just denied at every execute.

If you want even stronger containment — e.g. you suspect the agent's
process is compromised and shouldn't be allowed to call kubectl at all:

```bash
# Scale the api + mcp-server to 0. The agent is now off.
kubectl -n pocteo scale deploy/api --replicas=0
kubectl -n pocteo scale deploy/mcp-server --replicas=0

# Cluster events still fire, but nothing listens. Read-only inspection
# of past incidents is unaffected — the audit log + incident JSONs are
# already on disk.
```

Most extreme: `helm uninstall pocteo` — but only if you're certain the
agent itself is compromised. Reinstall later from backup
([disaster-recovery.md](disaster-recovery.md)).

## Investigation

For every surprising action, walk the audit chain to reconstruct WHY
the agent did it:

```bash
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
INC_ID="<incident_id from triage step 3>"

# Pull the full incident.
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost/api/incidents/$INC_ID" \
  | jq '{
      raw_event, diagnosis, proposed_action, policy_decision,
      action_result, verification, agent_mode, escalated
    }'
```

Decision tree:

```
What triggered the incident?
├── raw_event.source = "alertmanager" → external webhook claim;
│   verify the Alertmanager alert itself was legitimate
├── raw_event.source = "kubelet" → real K8s event; the agent
│   reacted to actual cluster state
└── raw_event has incident_id starting with `uc-` → an operator (or
    automation calling /api/usecases/.../run) explicitly triggered it.
    Identify WHO. The audit record's `source` field on the use-case
    event will say.

Why did it pick THAT action?
├── proposed_action.rationale + diagnosis.evidence → trace LLM's reasoning
├── runbook_hits → which runbook bias pushed it
└── If a `human_override=true` execute followed an L3/L4 denial, an
    operator approved it. Identify who via the IP / auth audit trail
    (we don't track operator identity in the bearer-token model — see
    "Going forward" below).

Was the action authorized?
├── policy_decision.allow = true → OPA matrix said yes, given the
│   severity that was passed in. Re-check whether the severity was
│   correctly derived from the event.
└── policy_decision.allow = false, but action_result exists →
    matrix said no but the operator clicked Approve. Forensic
    fingerprint: search audit for `human_override=true` on this incident.
```

## Resolution paths

### Action was correct but operator disagrees with policy

The matrix in `policies/agent.rego` is the source of truth. If you want
this kind of action to be blocked next time:

```bash
# Edit the matrix to raise the threshold.
vim policies/agent.rego
# Re-deploy.
kubectl create configmap pocteo-pocteo-opa \
  --from-file=agent.rego=policies/agent.rego \
  --dry-run=client -o yaml \
  | kubectl -n pocteo apply -f -
kubectl -n pocteo rollout restart deploy/opa
kubectl -n pocteo rollout status deploy/opa
```

Test against the policy test suite first (`make test-policy`) — same OPA
container, just with the test fixtures.

### Action was wrong; LLM picked badly

Same as [runaway-graph.md §B](runaway-graph.md) — fix the runbook that
biased the LLM, re-seed.

### Action was wrong; data feeding the LLM was wrong

E.g. a chaos experiment's labels misled the triage step. Fix the use case
template or the chaos label conventions; re-test.

### Action was right; underlying alert was bogus

The agent did the right thing on a bad input. Find the alert source
(Alertmanager rule, kubelet event source) and fix THAT — the agent
isn't the right place to filter false positives.

## After containment + investigation

Only once you understand WHY the unexpected action happened:

```bash
# Unfreeze. Note the resolution context so the audit chain shows the
# decision path.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: incident-response-agent — investigated, action allowed going forward"}}'

# OR keep it in observe-only for a while if you want to watch a few real
# events without execute. The graph still runs; OPA denies execute; you
# get audit + UI visibility without further side effects.
```

## Going forward

If you reach for this runbook more than once a quarter, the agent's
trust model needs tightening. Options:

1. **Per-operator bearer tokens.** Today the bearer token is shared.
   Move to short-lived per-operator JWTs so the audit log captures WHO
   approved an override. See [SECURITY.md §Hardening checklist](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md).
2. **Two-person rule.** Require 2 distinct tokens to approve any
   `human_override`. Lives in `agent/approvals.py::approve`; add an
   `approvals.json` schema field for `approved_by: list[str]` and
   require `len() >= 2` before calling MCP.
3. **Tighten the OPA matrix.** Move any actions you've had to override
   incorrectly toward L3/L4 so the default is deny. See
   [USE_CASES.md §6 Scenario 5](/docs/operations/ui-walkthrough) for the existing
   demo-by-design L4 path.

## Postmortem template

Use your org's standard security-incident template. Critical sections:

- **Timeline.** Audit log makes this almost free.
- **Authorization trace.** Show the chain: event source → triage
  severity → diagnose → plan_action → policy_decision → (maybe approve) → execute.
- **Whether the OPA matrix worked as designed.** If yes, the
  miss was the matrix policy itself. If no, file a P0 bug.
- **Trust-model improvements.** Pick from the "Going forward" list
  above + commit to a date.
