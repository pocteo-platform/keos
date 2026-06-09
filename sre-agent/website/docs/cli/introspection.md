---
id: introspection
slug: /cli/introspection
title: Agent introspection
sidebar_position: 7
description: keosctl policy · runbooks · bus — inspect what the agent sees, what it can do, and what's flowing through it.
---

# Agent introspection

Three command groups for looking under the hood of a running agent: the OPA policy matrix, the runbook corpus the agent retrieves from, and the live NATS event bus.

## `keosctl policy` — OPA matrix

The agent gates every action through Open Policy Agent. The matrix is keyed on `(action × severity × agent_mode)` + a hard-deny list for protected namespaces.

### `policy show`

Prints the loaded `agent.rego`. Reads from the policy ConfigMap (OPA's image is distroless so there's no `cat` inside the pod):

```bash
$ keosctl policy show
# OPA policy gating every action the self-healing agent proposes.
#
# Decision contract:
#   input  = {action, severity, agent_mode, target: {namespace, kind, name}}
#   output = data.agent.authz.allow  : bool
#            data.agent.authz.reasons: [string]  (denial explanations)
…
```

### `policy test <action> <severity> <namespace> <mode>`

Sends a hypothetical tuple to OPA and prints the decision:

```bash
$ keosctl policy test restart_pod high workloads active

OPA decision
────────────────────────────────────────────────────────────────
  › Input  action=restart_pod  severity=high  ns=workloads  mode=active
{
    "result": {
        "allow": true,
        "deny_reasons": [],
        "known_severities": ["critical", "high", "medium", "low"]
    }
}
```

Common test cases:

```bash
# Should ALLOW (active mode + non-protected ns + L1/L2 action)
keosctl policy test restart_pod high workloads active

# Should DENY (protected namespace)
keosctl policy test drain_node critical kube-system active

# Should DENY (observe-only blocks every mutation)
keosctl policy test restart_pod high workloads observe-only

# Should ALWAYS ALLOW (page_human is always permitted)
keosctl policy test page_human low workloads observe-only

# Should DENY (unknown action)
keosctl policy test nuke_cluster critical workloads active
```

Use this when:
- Adding a new action — check the matrix accepts it
- Onboarding a new namespace — verify the right actions are allowed there
- Debugging an unexpected escalation — replay the gate's input

## `keosctl runbooks` — corpus inspection

The agent retrieves from a Qdrant-backed corpus of markdown runbooks. These commands let you see what's loaded.

### `runbooks list`

```bash
$ keosctl runbooks list

Runbook corpus
────────────────────────────────────────────────────────────────
  • 001-memory-leak.md
  • 002-crashloop.md
  • 003-image-pull-failure.md
  • 004-node-pressure.md
  • 005-rollout-regression.md
  • 006-config-drift.md
  • 007-traffic-surge.md
  • 008-failed-scheduling.md
  • 009-traffic-forecast.md
  • 010-node-decay-prediction.md
  • 011-rightsizing.md
  • 012-drift.md

  Qdrant 'runbooks' collection: 12 points
```

The point count in Qdrant should match the file count. If it doesn't, run `keosctl seed`.

### `runbooks show <slug>`

Cat one runbook. The `.md` extension is optional:

```bash
keosctl runbooks show 001-memory-leak
keosctl runbooks show 005-rollout-regression.md
```

## `keosctl bus` — NATS event bus

Keos uses NATS JetStream as the internal event bus. `infra.events.<kind>` is the agent's primary input subject.

### `bus tail [subject]`

Live-subscribe to a NATS subject pattern. Routes through the api pod's `nats-py` (the `nats:alpine` image is too minimal to ship its own CLI):

```bash
# Default: every infra event
keosctl bus tail

# Specific event type
keosctl bus tail "infra.events.oom_killed"

# Wildcard
keosctl bus tail "infra.events.*_killed"
```

Output is streamed live:

```text
[infra.events.oom_killed] {"event_id":"e_2026-06-09…","pod":"checkout-api-abc","ns":"workloads",…}
[infra.events.crash_loop_back_off] {"event_id":"e_2026-06-09…","pod":"recommendation-svc-…",…}
```

Press Ctrl-C to stop.

### `bus streams`

JetStream stream summary:

```bash
$ keosctl bus streams

JetStream streams
────────────────────────────────────────────────────────────────
  STREAM                    MESSAGES   BYTES
  INFRA                         1247    824k
  ESCALATIONS                     12     8.2k
```

If JetStream isn't active or the monitoring port isn't exposed, you'll see a graceful "JetStream not active" message instead.

### `bus info`

Full NATS `/varz` dump (server stats, connections, slow consumers, etc.):

```bash
keosctl bus info | jq '.connections, .slow_consumers'
```

## When to use each

| You want to… | Command |
|---|---|
| Audit which actions OPA allows | `keosctl policy show` |
| Test a hypothetical action gate before deploying | `keosctl policy test` |
| Confirm the agent's runbook corpus is intact | `keosctl runbooks list` |
| Read what the agent retrieves for a class of incident | `keosctl runbooks show <slug>` |
| Watch real-time event traffic on the bus | `keosctl bus tail` |
| Check stream depth / lag | `keosctl bus streams` |
| Debug NATS connection issues | `keosctl bus info` |

## Common workflows

### "Why did this incident escalate?"

```bash
keosctl events -n 5                          # find the incident
keosctl incidents get <id>                   # full JSON
# Look at the proposed_action.tool and severity in the JSON.
# Replay the gate:
keosctl policy test <tool> <severity> <namespace> <mode>
```

### "Onboard a new namespace + verify the agent can act there"

```bash
# Add the namespace to the agent's allow-list (helm value)
keosctl values set agent.allowedNamespaces='{workloads,payments,new-team}'

# Test every action class can land there
for action in restart_pod rollout_undo scale_deployment cordon_node; do
  for severity in low medium high critical; do
    keosctl policy test $action $severity new-team active
  done
done
```

### "Add a new runbook and verify the agent retrieves it"

```bash
# Edit a runbook file in the cloned repo
vi sre-agent/agent/runbooks/013-my-custom-incident.md

# Re-seed
keosctl seed

# Confirm
keosctl runbooks list
# Should show 013 + Qdrant points = 13
```

### "Tap the event bus while running a use case"

```bash
# Terminal 1
keosctl bus tail "infra.events.>"

# Terminal 2
keosctl run lib-memory-leak

# Watch the event hit terminal 1 in real time
```

## See also

- [Agent commands](./agent) — `events`, `run`, `usecases`, `audit`
- [Data](./data) — `seed`, `models`
- [Architecture: the loop](../architecture/the-loop.md)
- [Architecture: safety model](../architecture/safety-model.md)
