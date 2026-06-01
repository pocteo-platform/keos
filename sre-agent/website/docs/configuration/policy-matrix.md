---
id: policy-matrix
slug: /configuration/policy-matrix
title: OPA policy matrix
sidebar_position: 3
description: The Rego policy matrix that gates every remediation. Easiest part of Keos to read and the most important to get right.
---

# OPA policy matrix

The single file `policies/agent.rego` (~50 lines) is the heart of
Keos's safety model. Every action the agent wants to take is
evaluated against this matrix before any side effect.

## The matrix

```
                 low    medium   high   critical
restart_pod      L1     L1       L2     L2
rollout_undo     L3     L2       L1     L1
scale_deployment L2     L1       L1     L1
cordon_node      L3     L2       L1     L1
drain_node       L4     L3       L2     L1
page_human       L1     L1       L1     L1
```

`allow if level <= 2`. Concretely:

- **L1 (always allowed)** — safe, reversible, expected actions for the
  severity at hand. `restart_pod` for any severity. `page_human` for
  any severity (just a notification).
- **L2 (allowed)** — slightly more invasive, but appropriate for the
  severity. `rollout_undo` at high severity. `drain_node` at high.
- **L3 (denied)** — over-react for the severity. `rollout_undo` at low
  severity (a real heavy bat for a minor problem). The agent escalates.
- **L4 (always denied)** — never allowed in autonomous mode. `drain_node`
  at low severity — that requires explicit human approval.

## Plus three hard-deny rules

1. **Protected namespaces.** Any target in `kube-system`, `kube-public`,
   `kube-node-lease`, `local-path-storage` → deny, regardless of severity
   or action.
2. **Mode gate.** `agent_mode != "active"` → deny. Allows `observe-only`
   to dry-run (the agent calls the policy_gate but the execute is a
   no-op).
3. **Unknown actions.** Any action not in the matrix → deny with reason
   `unknown action`. The MCP server can't be tricked into running a
   tool the policy doesn't recognize.

## Input shape

OPA receives this JSON from the agent's `policy_gate` node:

```json
{
  "input": {
    "action": "restart_pod",
    "severity": "high",
    "agent_mode": "active",
    "target": {
      "namespace": "workloads",
      "kind": "Pod",
      "name": "nginx-demo-7b8d5456d6-7shtv"
    }
  }
}
```

Output:

```json
{
  "result": {
    "allow": true,
    "reasons": []
  }
}
```

For a denied decision:

```json
{
  "result": {
    "allow": false,
    "reasons": ["L3 matrix denial: rollout_undo at severity=low requires L2 or lower"]
  }
}
```

The `reasons` array is the audit-loggable explanation. Every denial
carries human-readable why.

## Defense in depth — OPA is consulted twice

The agent's `policy_gate` calls OPA before routing to execute. The
MCP server **also calls OPA** before any kubectl call. Both must
allow. Reasons it's worth the extra HTTP call:

- Catches in-flight policy changes (between the agent's decision and
  the MCP server's action, a `kubectl apply -f policies/` could land).
- Catches a buggy agent that somehow bypassed its own policy_gate.
- Provides a single forensic record (in the MCP audit lane) that the
  exact action was authorized at the moment of execution.

## Test fixtures

`tests/test_policy.py` exercises the matrix against a real OPA
container with 17+ cases. Each case is a `{input, expected_allow,
expected_reason_substring}` triple. Run the test suite before any
policy edit:

```bash
make test-policy
```

The test container is the same `openpolicyagent/opa:0.69.0` image the
production chart uses, so behavior is identical.

## Editing the matrix

The Rego file is intentionally small. Adding a new action:

```rego
# policies/agent.rego (excerpt)
levels := {
    "restart_pod":      {"low": 1, "medium": 1, "high": 2, "critical": 2},
    "rollout_undo":     {"low": 3, "medium": 2, "high": 1, "critical": 1},
    "scale_deployment": {"low": 2, "medium": 1, "high": 1, "critical": 1},
    "cordon_node":      {"low": 3, "medium": 2, "high": 1, "critical": 1},
    "drain_node":       {"low": 4, "medium": 3, "high": 2, "critical": 1},
    "page_human":       {"low": 1, "medium": 1, "high": 1, "critical": 1},
    # NEW — add an entry, ship a test case, deploy:
    "exec_in_pod":      {"low": 4, "medium": 4, "high": 4, "critical": 4},
}
```

Then a corresponding `tests/test_policy.py` case to verify the matrix
denies the new action at every severity (since L4 is "never allowed
in autonomous mode"), and update the [tool catalog](#) in
`agent/tools.py` + `mcp_server/server.py`.

After the matrix is updated:

```bash
# Re-deploy the ConfigMap (Helm path):
kubectl create configmap pocteo-pocteo-opa \
  --from-file=agent.rego=policies/agent.rego \
  --dry-run=client -o yaml \
  | kubectl -n pocteo apply -f -
kubectl -n pocteo rollout restart deploy/opa
kubectl -n pocteo rollout status deploy/opa
```

## What's NOT in the matrix

By design, the matrix doesn't encode:

- **Time-of-day restrictions.** "Don't drain nodes between 09:00-17:00
  on a Tuesday." Such rules belong in a wrapping CRD or in operator
  hands, not in the action-severity matrix.
- **Per-tenant restrictions.** "Don't restart pods in namespace
  `finance` ever." Use the protected-namespaces deny-list, OR add a
  separate Rego rule, OR carve out a separate Helm release with its
  own RBAC.
- **Sequence rules.** "Don't `restart_pod` twice within 5 minutes for
  the same pod." Stateful — OPA is purposefully stateless. Sequence
  rules belong in the agent's graph (see the deduplication TODO in
  `agent/graph.py`).
- **Approval workflows.** "All `drain_node` actions need 2 operator
  sign-offs." That's the approvals queue in the SPA; see
  [Control panel](../operations/ui-walkthrough#approvals).

The matrix is for the simple, stateless, fast-path: "given THIS
action at THIS severity in THIS namespace, allow?" Everything else
is encoded above or below it.
