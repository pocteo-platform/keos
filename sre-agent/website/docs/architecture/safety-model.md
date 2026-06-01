---
id: safety-model
slug: /architecture/safety-model
title: Safety model
sidebar_position: 3
description: The three guards that prevent unauthorized cluster mutations, and how they fail.
---

# Safety model

The LLM never holds cluster credentials. Three independent guards sit
between any proposed action and the kubectl call. **All three must
allow.** Any one denies → the action escalates.

## Guard 1 — `AgentMode` CRD (kill switch)

`AgentMode` is a cluster-scoped Custom Resource:

```yaml
apiVersion: security.poc/v1
kind: AgentMode
metadata:
  name: default
spec:
  mode: observe-only   # observe-only | active | frozen
  note: "production-baseline"
```

The agent's `mode_store` runs a Kubernetes watch on this resource.
Every `execute` reads `await mode_store.get()` at the moment of
execution — so a `kubectl patch agentmode default
-p '{"spec":{"mode":"frozen"}}'` between events (or even mid-graph)
takes effect on the *next* execute.

| Mode | What the agent does |
|---|---|
| `observe-only` | Graph runs end-to-end. Every execute is dry-run — logged with `dry_run: true`, no kubectl call. Default mode after install. |
| `active` | Graph runs end-to-end. Allowed actions actually call kubectl. |
| `frozen` | OPA denies every action. Graph still runs (for audit) but every policy_gate returns `allow=false` with reason `mode=frozen`. |

**The frozen mode is the kill switch.** Flip it from anywhere with
kubectl access; effect is ~1 second.

If `mode_store` can't reach the kube API (network partition, RBAC
broken), the agent **defaults to the safest mode** (`observe-only` or
whatever `FALLBACK_AGENT_MODE` is set to). Never silently active.

## Guard 2 — OPA matrix

The Rego file (`policies/agent.rego`) is one file, ~50 lines. It
encodes a 6-action × 4-severity matrix:

|  | low | medium | high | critical |
|---|---|---|---|---|
| `restart_pod` | L1 | L1 | L2 | L2 |
| `rollout_undo` | L3 | L2 | L1 | L1 |
| `scale_deployment` | L2 | L1 | L1 | L1 |
| `cordon_node` | L3 | L2 | L1 | L1 |
| `drain_node` | L4 | L3 | L2 | L1 |
| `page_human` | L1 | L1 | L1 | L1 |

`allow if level <= 2`. So `rollout_undo` at `low` severity (L3) is
denied — the agent must escalate to a human. `restart_pod` at `high`
(L2) is allowed.

Beyond the matrix:

- **Protected namespaces.** `kube-system`, `kube-public`,
  `kube-node-lease`, `local-path-storage` — any action targeting these
  is hard-denied, regardless of severity.
- **Unknown actions.** Any action not in the matrix → hard-deny with
  reason `unknown action`. The MCP server can't be tricked into running
  a tool the policy doesn't recognize.
- **Mode gate.** `agent_mode != "active"` → hard-deny with reason
  `mode=<observed mode>`. Combined with `observe-only` this is what
  makes dry-run mode actually dry.

### Defense in depth

OPA is consulted **twice** per action:

1. The agent's `policy_gate` node calls OPA before deciding to route to
   execute or escalate.
2. The MCP server **also calls OPA** before running the kubectl call.

Both decisions must agree. If they diverge (e.g. an in-flight policy
change between step 1 and step 2), the action is denied and a loud
warning lands in the audit log. The two checks read the same
ConfigMap-mounted `agent.rego` so this is rare — but the cost of an
extra HTTP call is worth it.

## Guard 3 — scoped RBAC

The agent and the MCP server share one `ServiceAccount`:
`self-healing-agent` in the `security` namespace (or whatever the
Helm chart configures).

```yaml
# manifests/agent-rbac.yaml (excerpt)
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch", "delete", "patch"]
- apiGroups: ["apps"]
  resources: ["deployments", "deployments/scale"]
  verbs: ["get", "list", "patch", "update"]
- apiGroups: [""]
  resources: ["nodes"]
  verbs: ["get", "list", "patch", "update"]
```

This is **the only authority the agent has on the cluster**. Even if
the LLM somehow bypassed OPA and the MCP server, it could not:

- Create or modify Secrets / ConfigMaps
- Create or modify other ServiceAccounts / RoleBindings
- Touch CRDs (other than reading `AgentMode`)
- Talk to anything outside the cluster

For `kube-system` / `kube-public` actions: the SA's bindings don't
extend there at all. Even if OPA were misconfigured to allow such an
action, kubectl would 403.

## Audit chain (forensic property)

Every step of the loop writes one HMAC-SHA256-signed JSON line to
`agent/audit.log`. See the [audit chain](audit-chain) page for the
chaining formula and verification procedure.

The audit chain doesn't *prevent* a misbehaving agent. It guarantees
that any action the agent took — and any decision the policy gate
made — is forensically attributable after the fact.

## Failure modes

| What fails | What happens |
|---|---|
| `mode_store` can't reach kube API | Fall back to `observe-only` (configurable via `FALLBACK_AGENT_MODE`). Loud warning in logs. Never silently `active`. |
| OPA container is down | `policy_gate` fails closed. Every action escalates. The MCP server also fails closed. |
| MCP server is down | `execute` errors out → escalate. No way to call kubectl bypassing it. |
| Audit log volume is full | Write fails. The agent halts the graph for that incident and escalates with a loud reason. No partial audit. |
| HMAC key is wrong | The verify step would catch this; but since the key only signs (not verifies), a wrong key produces an unverifiable chain. Rotate via the [secret-rotation runbook](../runbooks/secret-rotation). |

## Going forward

The trust model has known limits, and we're explicit about them in
the release notes:

- The bearer token authenticating the SPA → API is shared (no
  per-operator JWT). Anyone with the token can flip the mode or
  approve an action. Item 7 in the SECURITY.md hardening checklist
  tracks per-operator auth.
- The audit chain depends entirely on `AUDIT_HMAC_KEY`. Treat it like
  a CA signing key.
- The MCP server can be reached from any pod in the namespace. A
  NetworkPolicy ships with the Helm chart to lock it down to api +
  frontend; verify it's applied in your environment.

See the full threat model in
[SECURITY.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md).
