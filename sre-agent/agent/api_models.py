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


# ─── Phase A — cluster context probe ─────────────────────────────────────


class NodeContext(BaseModel):
    """Richer per-node info than NodeSummary — used by the cluster-context
    probe so the SPA's setup wizard + dashboard cluster-context card can
    render node-level decisions (which node hosts the agent, which has a
    GPU, which is control-plane vs. worker)."""

    name: str
    role: str  # "control-plane" | "worker" | "unknown"
    ready: bool
    is_agent_home: bool
    cpu_capacity_m: int | None = None  # millicores
    memory_capacity_bytes: int | None = None
    gpu_capacity: int | None = None  # nvidia.com/gpu count
    labels: dict[str, str] = Field(default_factory=dict)
    taints: list[str] = Field(default_factory=list)


class DetectedOperators(BaseModel):
    """Well-known operators we probe for at boot. Helps the setup wizard
    suggest sensible defaults — e.g. enable drift detection when Argo CD
    is present, start baseline learning when Prometheus is present."""

    argocd: bool = False
    flux: bool = False
    chaos_mesh: bool = False
    prometheus: bool = False
    cert_manager: bool = False
    kube_state_metrics: bool = False
    # Security-domain telemetry sources (Phase 21). Present → the SPA can
    # show the security domain as "live eBPF" vs "synthetic only".
    falco: bool = False
    tetragon: bool = False


class AgentSelf(BaseModel):
    """Where the agent process itself is running, read at boot via the
    K8s downward API (KEOS_POD_NAMESPACE / KEOS_POD_NAME / KEOS_NODE_NAME
    env vars). Outside a K8s pod these are empty strings."""

    namespace: str = ""
    pod: str = ""
    node: str = ""


class RetentionDays(BaseModel):
    """Retention windows in days. Zero on any field means keep-forever."""

    audit: int = Field(ge=0, le=3650)
    incidents: int = Field(ge=0, le=3650)
    traces: int = Field(ge=0, le=3650)


class RetentionDiskUsage(BaseModel):
    """Per-bucket on-disk usage. Bytes + file count.

    Field names mirror the buckets in the pruner so the UI can
    correlate sliders 1:1 with usage rows."""

    audit_bytes: int = 0
    audit_files: int = 0
    incident_bytes: int = 0
    incident_files: int = 0
    trace_bytes: int = 0
    trace_files: int = 0
    calibration_bytes: int = 0
    baselines_bytes: int = 0
    total_bytes: int = 0


class RetentionState(BaseModel):
    """Full state for the Settings → Data Lifecycle tab.

    `source` tells the SPA whether the active config came from the
    operator's overlay or the env defaults — surfaced as a small
    chip next to the sliders so operators can see at a glance."""

    days: RetentionDays
    sweep_interval_s: int = Field(ge=60, le=86400)
    source: str  # "overlay" | "env"
    disk_usage: RetentionDiskUsage
    last_sweep_at: str | None = None
    next_sweep_at: str | None = None


class RetentionUpdateRequest(BaseModel):
    """PUT payload. Any field may be omitted; only the set fields
    update. Use `reset: true` to delete the overlay and revert to
    env defaults."""

    days: RetentionDays | None = None
    sweep_interval_s: int | None = Field(default=None, ge=60, le=86400)
    reset: bool = False


class ClusterContext(BaseModel):
    """One-shot snapshot of everything the agent knows about its host
    cluster + its own placement within it. Surfaced by GET
    /api/cluster/context for the dashboard's 'Cluster context' card,
    the first-time-setup wizard, and operator troubleshooting."""

    distro: str  # "k3s" | "k3d" | "talos" | "rke2" | "kubeadm" | "gke" | "eks" | "aks" | "unknown"
    runtime: str  # "containerd" | "crio" | "docker" | "unknown"
    kubernetes_version: str  # the kubelet's reported version
    node_count: int
    ready_node_count: int
    total_cpu_m: int  # cluster-total millicores
    total_memory_bytes: int  # cluster-total bytes
    total_gpu_count: int  # cluster-total nvidia.com/gpu
    nodes: list[NodeContext] = Field(default_factory=list)
    detected_operators: DetectedOperators = Field(default_factory=DetectedOperators)
    agent_self: AgentSelf = Field(default_factory=AgentSelf)
    warnings: list[str] = Field(default_factory=list)


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
    # Phase 21 — which agent domain handled this incident. The SPA renders a
    # badge (SRE vs Security) and can filter the list by it.
    domain: Literal["sre", "security"] = "sre"
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
# Forecasts (Phase 14.5) — per-pod horizon projections for the SPA.
# Different shape from BaselineSnapshotModel: forecast rows are
# explicitly about TREND, not current state.
# ---------------------------------------------------------------------------


class ForecastRow(BaseModel):
    metric: str
    namespace: str
    scope: str
    # Status mirrors the forecaster's classifier output. `none` covers
    # the case where the baseline exists but the linear fit failed
    # (constant series, R² below floor, < N_MIN_FIT samples).
    level: str  # safe | watch | warning | critical | none
    slope_per_s: float | None = None
    r2: float | None = None
    t5_min: float | None = None
    t15_min: float | None = None
    t60_min: float | None = None
    sigma_above: float | None = None
    # Snapshot's current value, copied through so the SPA doesn't have
    # to call /api/baselines separately to render the sparkline endpoint.
    current: float | None = None
    mean: float | None = None
    std: float | None = None
    n_samples: int = 0


class ForecastList(BaseModel):
    total: int
    items: list[ForecastRow]


# ---------------------------------------------------------------------------
# Scene (Phase 14.5) — what the agent DID during one incident, projected
# for the use-case-launch UI. Aggregates: resources affected (synthesized
# from action_result.args), the chain of decisions (triage→...→verify),
# the outcome banner data (healed vs escalated + reason), and a few
# operator-facing summary fields.
# ---------------------------------------------------------------------------


class SceneResource(BaseModel):
    """One K8s object the agent (or its observer / use-case runner)
    touched during this incident. `state` is the operator-readable
    status now — e.g., `Running`, `CrashLoopBackOff`, `cordoned`,
    `(evicted)`."""

    kind: str  # Pod | Deployment | Node | ConfigMap | ChaosExperiment | ...
    namespace: str | None = None
    name: str
    state: str
    # `outcome` is the agent's verdict on THIS resource after the
    # incident ran. Drives the row color in the SPA:
    #   reconciled — agent's action succeeded + verify=healed
    #   paged      — agent escalated to a human; no mutation done
    #   failed     — action ran but verify=False (or action_result.success=False)
    #   unchanged  — observed but not acted on (e.g. the chaos CR)
    outcome: str  # reconciled | paged | failed | unchanged
    # Optional pointer back to the audit event that touched this row.
    audit_event_type: str | None = None


class SceneStep(BaseModel):
    """One graph node's entry in the action chain. Sourced from the
    audit log — every graph node emits one of these via `emit()`."""

    name: str  # triage | retrieve_context | enrich_with_baselines | diagnose | ...
    ts: str  # ISO timestamp
    summary: str  # one-line human-readable
    status: str  # ok | warn | error | skipped
    payload_keys: list[str] = []  # so the SPA can decide whether to render a "view JSON" button


class SceneOutcome(BaseModel):
    """The big visual answer on the use-case-launch banner."""

    verdict: str  # healed | escalated | failed | observe_only
    reason: str  # human-readable; goes into the banner subtitle
    time_to_outcome_seconds: float | None = None
    # Per-class detail for the operator:
    confidence: float | None = None  # diagnose confidence at the time
    rule: str | None = None
    tool: str | None = None  # which action ran (or would have run)
    policy_decision: dict[str, object] | None = None  # full OPA payload


class WorkloadScene(BaseModel):
    """Returned by `GET /api/scene/{incident_id}`. The use-case-launch
    UI binds the live pipeline's right-hand pane to a WS-pushed
    version of this same shape so the operator sees the scene
    populate row by row as the graph runs."""

    incident_id: str
    namespace: str | None = None
    pod: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    outcome: SceneOutcome
    resources: list[SceneResource] = []
    steps: list[SceneStep] = []


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


class UseCaseAttackSim(BaseModel):
    """Phase 21 — a real attack simulation that fires LIVE eBPF telemetry.

    When a security use case has an `attack_sim`, the runner applies a short-
    lived K8s Job (TTL-cleaned) that actually performs the suspicious action
    (`kubectl exec … bash -i`, run xmrig, mount the docker socket, curl an
    external IP) so Falco/Tetragon detect it for real. When absent — or when
    the cluster has no eBPF telemetry installed — the runner falls back to
    the synthetic event so the demo always works.

    `kind` picks the simulation shape; `command` overrides the default for
    advanced operators.
    """

    kind: Literal["exec_shell", "run_miner", "sensitive_mount", "egress"] = "exec_shell"
    command: list[str] = Field(default_factory=list)
    image: str = "busybox:1.36"


class UseCase(BaseModel):
    id: str
    name: str
    description_en: str = ""
    description_fr: str = ""
    tags: list[str] = Field(default_factory=list)
    # Phase 21 — which agent domain this scenario drives. "sre" (default,
    # back-compat) publishes to infra.events.*; "security" publishes to
    # security.events.* and may carry an attack_sim.
    domain: Literal["sre", "security"] = "sre"
    target: UseCaseTarget
    falco: UseCaseFalcoTemplate
    expectation: UseCaseExpectation = Field(default_factory=UseCaseExpectation)
    attack_sim: UseCaseAttackSim | None = None
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
