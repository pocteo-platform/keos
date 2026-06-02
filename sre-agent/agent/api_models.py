"""Pydantic request/response models for the FastAPI app.

Kept separate from `agent/schemas.py` so internal graph types (IncidentState
TypedDict, ProposedAction, etc.) don't leak into the public API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.schemas import AgentModeLiteral, Severity, ToolName

# ---------------------------------------------------------------------------
# Health + mode
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    ok: bool = True
    agent_mode: str | None = None
    version: str = "0.1.0"


class ModeResponse(BaseModel):
    mode: AgentModeLiteral
    updated_at: datetime | None = None
    updated_by: str | None = None


class ModeChangeRequest(BaseModel):
    mode: AgentModeLiteral
    note: str | None = None


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


class NodeSummary(BaseModel):
    name: str
    ready: bool
    version: str
    taints: list[str] = Field(default_factory=list)


class NamespaceSummary(BaseModel):
    name: str
    age_seconds: int
    pod_count: int


class PodSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    namespace: str
    phase: str
    ready: str  # "X/Y"
    restarts: int
    age_seconds: int
    owner_kind: str | None = None
    owner_name: str | None = None
    last_event: str | None = None


class DeploymentSummary(BaseModel):
    name: str
    namespace: str
    replicas_desired: int
    replicas_ready: int
    replicas_updated: int
    age_seconds: int
    images: list[str] = Field(default_factory=list)
    available: bool


class EventSummary(BaseModel):
    """Kubernetes Event as the API surfaces it."""

    namespace: str
    name: str
    type: str  # Normal / Warning
    reason: str
    message: str
    involved_kind: str | None
    involved_name: str | None
    count: int
    first_seen: str | None
    last_seen: str | None


class ActiveIncident(BaseModel):
    incident_id: str
    rule: str | None = None
    severity: str | None = None
    pod: str | None = None
    namespace: str | None = None
    started_at: str | None = None


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class IncidentSummary(BaseModel):
    incident_id: str
    ts: datetime
    rule: str
    severity: Severity
    namespace: str | None
    pod: str | None
    diagnosis_summary: str | None = None
    proposed_tool: ToolName | None = None
    policy_allow: bool | None = None
    healed: bool | None = None
    escalated: bool
    agent_mode: AgentModeLiteral | None = None
    # True when the graph short-circuited at triage because the event was
    # stale + the target pod was already Ready. UI shows a distinct
    # "Auto-resolved" outcome chip for these instead of "Escalated".
    stale_resolved: bool = False


class IncidentDetail(IncidentSummary):
    raw_event: dict[str, Any] | None = None
    runbook_hits: list[dict[str, Any]] = Field(default_factory=list)
    k8sgpt_findings: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis: dict[str, Any] | None = None
    proposed_action: dict[str, Any] | None = None
    policy_decision: dict[str, Any] | None = None
    action_result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    pipeline_trace: list[dict[str, Any]] = Field(default_factory=list)


class IncidentList(BaseModel):
    total: int
    items: list[IncidentSummary]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditRecord(BaseModel):
    ts: datetime
    incident_id: str
    event_type: str
    payload: dict[str, Any]
    hmac: str
    hmac_valid: bool


class AuditList(BaseModel):
    total: int
    items: list[AuditRecord]


class AuditVerifyResponse(BaseModel):
    valid: bool
    record: AuditRecord | None = None


# ---------------------------------------------------------------------------
# Calibration (Phase 12) — per-rule confidence statistics + threshold.
# ---------------------------------------------------------------------------


class CalibrationRule(BaseModel):
    """One row of the calibration store, projected for the SPA."""

    rule_id: str
    n_total: int
    n_graded: int
    n_correct: int
    n_escalated_lowconf: int
    n_escalated_other: int
    n_stale: int
    n_override: int
    accuracy: float | None
    mean_confidence: float | None
    p50_confidence: float
    p90_confidence: float
    threshold_override: float | None
    learned_threshold: float
    effective_threshold: float
    updated_at: str


class CalibrationList(BaseModel):
    total: int
    items: list[CalibrationRule]
    median_confidence: float | None = None
    pct_above_90: float | None = None


class CalibrationThresholdRequest(BaseModel):
    """Operator-pinned override. `null` clears the override."""

    threshold: float | None
    note: str | None = None


# ---------------------------------------------------------------------------
# Baselines (Phase 13) — per-metric snapshots returned to the SPA + scripts.
# ---------------------------------------------------------------------------


class BaselineSnapshotModel(BaseModel):
    """One row in the per-pod / per-metric baseline read view.

    `status` is one of `ok | learning | missing`. `current` is the
    most-recent sample value; baseline fields are present only when
    `status == "ok"`. `sigma_above` is bounded — see
    `agent.baselines.math.safe_zscore`.
    """

    metric: str
    namespace: str
    scope: str
    status: str
    current: float | None = None
    mean: float | None = None
    std: float | None = None
    sigma_above: float | None = None
    seasonal_residual: float | None = None
    n_samples: int = 0


class BaselineList(BaseModel):
    """Returned by `GET /api/baselines?ns=&pod=`. `total_samples` is
    the count of raw samples currently in the store for ALL metrics —
    the doctor / SPA banner uses it to render the warmup progress."""

    total: int
    items: list[BaselineSnapshotModel]
    total_samples: int = 0
    keys_known: int = 0


# ---------------------------------------------------------------------------
# Scenarios + use cases
# ---------------------------------------------------------------------------


class ScenarioDescriptor(BaseModel):
    id: int
    rule: str
    priority: str
    subject: str
    description: str = ""


class ScenarioRunRequest(BaseModel):
    scenario_id: int
    overrides: dict[str, Any] | None = None  # future: override pod/namespace


class ScenarioRunResponse(BaseModel):
    incident_id: str
    subject: str


class UseCaseExpectation(BaseModel):
    diagnosis_keywords: list[str] = Field(default_factory=list)
    proposed_tool: ToolName | None = None
    policy_allow: bool | None = None
    outcome: Literal["healed", "escalated"] | None = None


class UseCaseFalcoTemplate(BaseModel):
    rule: str
    priority: Literal["Notice", "Warning", "Error", "Critical"]
    output_template: str = ""
    output_fields: dict[str, str] = Field(default_factory=dict)


class UseCaseTarget(BaseModel):
    namespace: str
    pod_name: str | None = None
    pod_label_selector: dict[str, str] | None = None
    owner_kind: Literal["Deployment", "StatefulSet", "DaemonSet", "Standalone"] = "Deployment"


class UseCase(BaseModel):
    id: str
    name: str
    description_en: str = ""
    description_fr: str = ""
    tags: list[str] = Field(default_factory=list)
    target: UseCaseTarget
    falco: UseCaseFalcoTemplate
    expectation: UseCaseExpectation = Field(default_factory=UseCaseExpectation)
    runbook_en: str = ""
    runbook_fr: str = ""
    created_at: datetime
    updated_at: datetime


class UseCaseRunResponse(BaseModel):
    incident_id: str
    use_case_id: str


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class PendingApproval(BaseModel):
    incident_id: str
    ts: datetime
    proposed_action: dict[str, Any]
    severity: Severity
    namespace: str | None
    denied_reasons: list[str]


class ApprovalRequest(BaseModel):
    incident_id: str


class ApprovalResponse(BaseModel):
    approved: bool
    executed: bool = False
    action_result: dict[str, Any] | None = None
    reason: str | None = None


class DenialRequest(BaseModel):
    incident_id: str
    reason: str
