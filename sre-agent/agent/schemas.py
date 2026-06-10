"""Pydantic v2 models and TypedDict state for the agent.

The LangGraph state is a TypedDict (not a BaseModel) — LangGraph 0.2 merges
partial dict updates from each node, and a TypedDict gives us type hints
without paying for runtime validation on every transition.

LLM responses ARE validated with pydantic. The LLM is allowed to be wrong;
pydantic + a retry path catches malformed outputs and escalates if a model
can't recover.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["low", "medium", "high", "critical"]
AgentModeLiteral = Literal["observe-only", "active", "frozen"]
# Phase 21 — Keos runs two agent domains over one graph. The discriminator
# is set in triage from the NATS subject family: infra.events.* → "sre",
# security.events.* → "security". Defaults to "sre" so every pre-Phase-21
# incident + the back-compat code paths keep working unchanged.
Domain = Literal["sre", "security"]
ToolName = Literal[
    # ── SRE domain ──────────────────────────────────────────────────────
    "restart_pod",
    "rollout_undo",
    "scale_deployment",
    "cordon_node",
    "uncordon_node",
    "drain_node",
    # Phase 14 — predictive / rightsizing tools.
    "tune_hpa",
    "set_resources",
    "evict_workload",
    # Phase 15 — GitOps drift revert.
    "revert_drift",
    "page_human",
    # ── Security domain (Phase 21) ──────────────────────────────────────
    # Containment + response actions. Escalate-first: these are gated to
    # active mode + high/critical + non-protected namespaces in the OPA
    # matrix, and isolate_pod / apply_network_policy are reversible.
    "isolate_pod",  # apply a deny-all NetworkPolicy + quarantine label
    "apply_network_policy",  # deny-egress NetworkPolicy (data-exfil containment)
    "kill_pod",  # force-delete a compromised pod
    "quarantine_pod",  # label + isolate + annotate for forensics hold
    "capture_forensics",  # annotate + dump recent logs into the audit trail
    "page_security",  # page the security / IR channel (always allowed)
]


# ---------------------------------------------------------------------------
# Falco event normalization (sources may be jagged; the agent reads a clean view).
# ---------------------------------------------------------------------------


class TriagedEvent(BaseModel):
    """Normalised view of an incoming Falco-shaped event."""

    model_config = ConfigDict(extra="ignore")

    incident_id: str
    rule: str
    severity: Severity
    namespace: str | None
    pod: str | None
    container: str | None
    process: str | None
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# LLM response schemas. Validated; on ValidationError the graph retries once,
# then escalates.
# ---------------------------------------------------------------------------


class Diagnosis(BaseModel):
    """Output of the diagnose node."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(..., min_length=1, max_length=400)
    root_cause: str = Field(..., min_length=1, max_length=400)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    # Optional reconciliation note for when the Falco event and the K8sGPT
    # findings tell different stories. Small models don't always emit this
    # when there's no conflict, so we default to empty string and treat
    # absence as "no conflict to flag".
    notes: str = Field(default="", max_length=400)


class ProposedAction(BaseModel):
    """Output of the plan_action node."""

    model_config = ConfigDict(extra="ignore")

    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    scope: Literal["pod", "ns", "deploy", "node", "n/a"]
    reversible: bool
    rationale: str = Field(..., min_length=1, max_length=400)


# ---------------------------------------------------------------------------
# Decision documents from non-LLM components.
# ---------------------------------------------------------------------------


class PolicyDecision(BaseModel):
    """Response from the OPA policy gate."""

    allow: bool
    reasons: list[str] = Field(default_factory=list)


class ActionResult(BaseModel):
    """Outcome of the execute node."""

    tool: ToolName
    args: dict[str, Any]
    dry_run: bool
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    # Optional per-tool side-channel info. page_human uses this to record
    # which notification channels (Slack, generic webhook) actually got
    # the page, so the audit chain shows the delivery outcome.
    meta: dict[str, Any] | None = None


class Verification(BaseModel):
    """Outcome of the verify node — was the remediation effective?

    `healed=None` means "unknown / not applicable" — used for actions
    that delegate the heal to a human (page_human) where the cluster
    state is unchanged by the agent and the operator owns the next step.
    """

    healed: bool | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    notes: str = ""


class Finding(BaseModel):
    """One structured finding from K8sGPT's analyzers.

    K8sGPT returns one of these per problem detected (a pod terminating
    badly, a Deployment with an unresolvable image, etc.). The agent's
    diagnose node receives a filtered list (only findings touching the
    incident's target pod / owner) alongside the raw Falco event and the
    RAG runbook hits.
    """

    model_config = ConfigDict(extra="ignore")

    kind: str
    name: str
    error: str
    parent_object: str | None = None
    details: str = ""


# ---------------------------------------------------------------------------
# LangGraph state. Each node returns a partial dict that LangGraph merges in.
# total=False means every key is optional — the graph fills them in as it goes.
# ---------------------------------------------------------------------------


class IncidentState(TypedDict, total=False):
    # Always present (set in triage).
    incident_id: str
    raw_event: dict[str, Any]
    severity: Severity
    namespace: str
    pod: str
    container: str | None
    rule: str
    # Phase 21 — which agent domain is handling this incident. Set in
    # triage from the event source/subject; drives which system prompt,
    # which tool catalog, and which RAG tag filter the graph uses.
    # Absent → treated as "sre" (back-compat).
    domain: Domain

    # Filled in as the graph progresses.
    agent_mode: AgentModeLiteral
    context: dict[str, Any]
    k8sgpt_findings: list[dict[str, Any]]
    # Phase 13 — per-pod metric baselines (EWMA + per-hour seasonal
    # residual). Always present after `enrich_with_baselines`; may be
    # an empty list when the learner has no data for this pod yet, or
    # when the store / Prometheus is unconfigured.
    baselines: list[dict[str, Any]]
    diagnosis: dict[str, Any]
    proposed_action: dict[str, Any]
    policy_decision: dict[str, Any]
    action_result: dict[str, Any]
    verification: dict[str, Any]

    # Terminal flags.
    escalated: bool
    escalation_reason: str

    # Set by triage when the event is older than STALE_EVENT_S AND the
    # target pod is currently Ready (or gone). Causes the graph to skip
    # straight to escalate WITHOUT calling page_human — the underlying
    # problem already resolved itself, no human attention needed.
    stale_resolved: bool
    stale_reason: str  # "already_ready" or "pod_gone"
    staleness_age_s: int
