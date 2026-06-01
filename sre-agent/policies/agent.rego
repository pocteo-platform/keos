# OPA policy gating every action the self-healing agent proposes.
#
# Decision contract:
#   input  = {action, severity, agent_mode, target: {namespace, kind, name}}
#   output = data.agent.authz.allow  : bool
#            data.agent.authz.reasons: [string]  (denial explanations, empty if allow)
#
# Allow logic = action matrix lookup; L1/L2 = allow, L3/L4 = deny.
# Three hard-deny gates that override the matrix:
#   1. protected namespace
#   2. agent_mode != "active"
#   3. unknown action / severity
#
# The matrix is embedded in this file so the policy is self-contained — no
# external data document, no plugin loading. Change here, restart OPA, done.
package agent.authz

import rego.v1

# levels[action][severity] -> integer 1..4 (lower = safer / always allow).
# SRE action catalog (rev. Phase 9.2 — replaces the security-flavoured
# isolate_pod / apply_network_policy with scale_deployment / cordon_node /
# drain_node).
levels := {
	"restart_pod":       {"low": 1, "medium": 1, "high": 2, "critical": 2},
	"rollout_undo":      {"low": 3, "medium": 2, "high": 1, "critical": 1},
	"scale_deployment":  {"low": 2, "medium": 1, "high": 1, "critical": 1},
	"cordon_node":       {"low": 3, "medium": 2, "high": 1, "critical": 1},
	# uncordon is the inverse of cordon. Lower blast radius (only re-enables
	# scheduling, doesn't evict anything). Generous matrix because failed
	# scheduling caused by a stale cordon is the most common "stuck cluster"
	# state and the agent should be able to recover it autonomously.
	"uncordon_node":     {"low": 1, "medium": 1, "high": 1, "critical": 1},
	"drain_node":        {"low": 4, "medium": 3, "high": 2, "critical": 1},
	"page_human":        {"low": 1, "medium": 1, "high": 1, "critical": 1},
}

# Namespaces the agent must never touch, regardless of matrix outcome.
# `kube-system` and `kube-public` are control-plane; `security` removed (no
# longer a namespace concept in the SRE pivot, but we keep the rule generic
# enough that any future "protected" set is a one-line change).
protected_namespaces := {"kube-system", "kube-public"}

# Severities we know about. Any other value is itself a denial.
known_severities := {"low", "medium", "high", "critical"}

# ---------------------------------------------------------------------------
# Default + aggregate decision.
# ---------------------------------------------------------------------------

default allow := false

allow if count(reasons) == 0

# `reasons` is the union of every deny_reason that fires. Surfaced verbatim
# for the audit log so the operator can see WHY a request was blocked.
reasons contains msg if some msg in deny_reasons

# ---------------------------------------------------------------------------
# Hard-deny gate 1: unknown action.
# ---------------------------------------------------------------------------
deny_reasons contains msg if {
	not levels[input.action]
	msg := sprintf("unknown action %q (not in catalog)", [input.action])
}

# ---------------------------------------------------------------------------
# Hard-deny gate 2: unknown severity (only checked if the action IS known,
# to avoid double-reporting on a completely bogus input).
# ---------------------------------------------------------------------------
deny_reasons contains msg if {
	levels[input.action]
	not input.severity in known_severities
	msg := sprintf("unknown severity %q", [input.severity])
}

# ---------------------------------------------------------------------------
# Hard-deny gate 3: protected namespace.
# ---------------------------------------------------------------------------
deny_reasons contains msg if {
	input.target.namespace in protected_namespaces
	msg := sprintf("namespace %q is protected (kube-system/kube-public/security are off-limits)", [input.target.namespace])
}

# ---------------------------------------------------------------------------
# Hard-deny gate 4: agent_mode must be "active". observe-only/frozen/anything
# else means the agent is in dry-run or paused and must not execute.
# ---------------------------------------------------------------------------
deny_reasons contains msg if {
	input.agent_mode != "active"
	msg := sprintf("agent_mode is %q (must be \"active\" to execute)", [input.agent_mode])
}

# ---------------------------------------------------------------------------
# Matrix gate: level >= 3 -> deny.
# Only fires when the action is in the matrix AND severity is known, so we
# don't pile on top of the gates above.
# ---------------------------------------------------------------------------
deny_reasons contains msg if {
	level := levels[input.action][input.severity]
	level >= 3
	msg := sprintf("action %q at severity %q is L%d (max allowed L2)", [input.action, input.severity, level])
}
