// Typed REST client. Every method calls fetch() with the bearer token from
// the Zustand store; 401 responses clear the token and throw a typed error.

import { useApp } from "./store";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = useApp.getState().token;
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 401) {
    useApp.getState().setToken(null);
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------- response shapes (mirror agent/api_models.py) ----------

export interface HealthResponse {
  ok: boolean;
  agent_mode: string | null;
  version: string;
}

export type AgentMode = "observe-only" | "active" | "frozen";

export interface ModeResponse {
  mode: AgentMode;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface NodeSummary {
  name: string;
  ready: boolean;
  version: string;
  taints: string[];
}

// ─── Phase A — cluster context probe ────────────────────────────────────

export interface NodeContext {
  name: string;
  role: "control-plane" | "worker" | "unknown";
  ready: boolean;
  is_agent_home: boolean;
  cpu_capacity_m: number | null;
  memory_capacity_bytes: number | null;
  gpu_capacity: number | null;
  labels: Record<string, string>;
  taints: string[];
}

export interface DetectedOperators {
  argocd: boolean;
  flux: boolean;
  chaos_mesh: boolean;
  prometheus: boolean;
  cert_manager: boolean;
  kube_state_metrics: boolean;
}

export interface AgentSelf {
  namespace: string;
  pod: string;
  node: string;
}

export interface ClusterContext {
  distro: string;
  runtime: string;
  kubernetes_version: string;
  node_count: number;
  ready_node_count: number;
  total_cpu_m: number;
  total_memory_bytes: number;
  total_gpu_count: number;
  nodes: NodeContext[];
  detected_operators: DetectedOperators;
  agent_self: AgentSelf;
  warnings: string[];
}

export interface NamespaceSummary {
  name: string;
  age_seconds: number;
  pod_count: number;
}

export interface PodSummary {
  name: string;
  namespace: string;
  phase: string;
  ready: string;
  restarts: number;
  age_seconds: number;
  owner_kind: string | null;
  owner_name: string | null;
  last_event: string | null;
}

export interface DeploymentSummary {
  name: string;
  namespace: string;
  replicas_desired: number;
  replicas_ready: number;
  replicas_updated: number;
  age_seconds: number;
  images: string[];
  available: boolean;
}

export interface EventSummary {
  namespace: string;
  name: string;
  type: "Normal" | "Warning" | string;
  reason: string;
  message: string;
  involved_kind: string | null;
  involved_name: string | null;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ActiveIncident {
  incident_id: string;
  rule: string | null;
  severity: string | null;
  pod: string | null;
  namespace: string | null;
  started_at: string | null;
}

export interface IncidentSummary {
  incident_id: string;
  ts: string;
  rule: string;
  severity: "low" | "medium" | "high" | "critical";
  namespace: string | null;
  pod: string | null;
  diagnosis_summary: string | null;
  proposed_tool: string | null;
  policy_allow: boolean | null;
  healed: boolean | null;
  escalated: boolean;
  agent_mode: AgentMode | null;
  /** True when the graph short-circuited at triage because the event
   *  was stale (older than STALE_EVENT_S) AND the target pod was
   *  currently Ready. UI shows "Auto-resolved" instead of "Escalated". */
  stale_resolved: boolean;
}

export interface IncidentDetail extends IncidentSummary {
  raw_event?: Record<string, unknown> | null;
  runbook_hits: Array<Record<string, unknown>>;
  k8sgpt_findings: Array<Record<string, unknown>>;
  diagnosis?: Record<string, unknown> | null;
  proposed_action?: Record<string, unknown> | null;
  policy_decision?: Record<string, unknown> | null;
  action_result?: Record<string, unknown> | null;
  verification?: Record<string, unknown> | null;
  pipeline_trace: Array<Record<string, unknown>>;
}

export interface IncidentList {
  total: number;
  items: IncidentSummary[];
}

export interface AuditRecord {
  ts: string;
  incident_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  hmac: string;
  hmac_valid: boolean;
}

export interface AuditList {
  total: number;
  items: AuditRecord[];
}

export interface ScenarioDescriptor {
  id: number;
  rule: string;
  priority: string;
  subject: string;
  description?: string;
}

export interface PendingApproval {
  incident_id: string;
  ts: string;
  proposed_action: Record<string, unknown>;
  severity: string;
  namespace: string | null;
  denied_reasons: string[];
}

// ─── Calibration (Phase 12) ─────────────────────────────────────────────
export interface CalibrationRule {
  rule_id: string;
  n_total: number;
  n_graded: number;
  n_correct: number;
  n_escalated_lowconf: number;
  n_escalated_other: number;
  n_stale: number;
  n_override: number;
  accuracy: number | null;
  mean_confidence: number | null;
  p50_confidence: number;
  p90_confidence: number;
  threshold_override: number | null;
  learned_threshold: number;
  effective_threshold: number;
  updated_at: string;
}

export interface CalibrationList {
  total: number;
  items: CalibrationRule[];
  median_confidence: number | null;
  pct_above_90: number | null;
}

export interface UseCase {
  id: string;
  name: string;
  description_en: string;
  description_fr: string;
  tags: string[];
  target: {
    namespace: string;
    pod_name?: string | null;
    pod_label_selector?: Record<string, string> | null;
    owner_kind: "Deployment" | "StatefulSet" | "DaemonSet" | "Standalone";
  };
  falco: {
    rule: string;
    priority: "Notice" | "Warning" | "Error" | "Critical";
    output_template: string;
    output_fields: Record<string, string>;
  };
  expectation: {
    diagnosis_keywords: string[];
    proposed_tool?: string | null;
    policy_allow?: boolean | null;
    outcome?: "healed" | "escalated" | null;
  };
  runbook_en: string;
  runbook_fr: string;
  created_at: string;
  updated_at: string;
}

// ---------- Phase 13: baselines ----------

export interface BaselineSnapshotModel {
  metric: string;
  namespace: string;
  scope: string;
  status: "ok" | "learning" | "missing";
  current: number | null;
  mean: number | null;
  std: number | null;
  sigma_above: number | null;
  seasonal_residual: number | null;
  n_samples: number;
}

export interface BaselineList {
  total: number;
  items: BaselineSnapshotModel[];
  total_samples: number;
  keys_known: number;
}

// ---------- Phase 14.5: forecasts + scene ----------

export type ForecastLevel = "critical" | "warning" | "watch" | "safe" | "none";

export interface ForecastRow {
  metric: string;
  namespace: string;
  scope: string;
  level: ForecastLevel;
  slope_per_s: number | null;
  r2: number | null;
  t5_min: number | null;
  t15_min: number | null;
  t60_min: number | null;
  sigma_above: number | null;
  current: number | null;
  mean: number | null;
  std: number | null;
  n_samples: number;
}

export interface ForecastList {
  total: number;
  items: ForecastRow[];
}

export type SceneOutcomeVerdict = "healed" | "escalated" | "failed" | "observe_only" | "unknown";

export type SceneResourceOutcome = "reconciled" | "paged" | "failed" | "unchanged";

export interface SceneResource {
  kind: string;
  namespace: string | null;
  name: string;
  state: string;
  outcome: SceneResourceOutcome;
  audit_event_type: string | null;
}

export interface SceneStep {
  name: string;
  ts: string;
  summary: string;
  status: "ok" | "warn" | "error" | "skipped";
  payload_keys: string[];
}

export interface SceneOutcome {
  verdict: SceneOutcomeVerdict;
  reason: string;
  time_to_outcome_seconds: number | null;
  confidence: number | null;
  rule: string | null;
  tool: string | null;
  policy_decision: Record<string, unknown> | null;
}

export interface WorkloadScene {
  incident_id: string;
  namespace: string | null;
  pod: string | null;
  started_at: string | null;
  completed_at: string | null;
  outcome: SceneOutcome;
  resources: SceneResource[];
  steps: SceneStep[];
}

// ---------- endpoint helpers ----------

export const api = {
  health: () => request<HealthResponse>("GET", "/api/health"),
  getMode: () => request<ModeResponse>("GET", "/api/mode"),
  setMode: (mode: AgentMode, note?: string) =>
    request<ModeResponse>("POST", "/api/mode", { mode, note }),

  listNodes: () => request<NodeSummary[]>("GET", "/api/cluster/nodes"),
  listNamespaces: () => request<NamespaceSummary[]>("GET", "/api/cluster/namespaces"),
  listPods: (ns?: string) =>
    request<PodSummary[]>(
      "GET",
      ns ? `/api/cluster/pods?ns=${encodeURIComponent(ns)}` : "/api/cluster/pods",
    ),
  listDeployments: (ns: string) =>
    request<DeploymentSummary[]>("GET", `/api/cluster/deployments?ns=${encodeURIComponent(ns)}`),
  listEvents: (ns: string, limit = 50) =>
    request<EventSummary[]>(
      "GET",
      `/api/cluster/events?ns=${encodeURIComponent(ns)}&limit=${limit}`,
    ),
  listActiveIncidents: () => request<ActiveIncident[]>("GET", "/api/incidents/active"),

  // Phase A — cluster context probe. Used by the dashboard's
  // "Cluster context" card + (future) the first-time-setup wizard.
  getClusterContext: () => request<ClusterContext>("GET", "/api/cluster/context"),

  // Phase 12 — calibration
  listCalibration: () => request<CalibrationList>("GET", "/api/calibration"),
  getCalibrationRule: (rule: string) =>
    request<CalibrationRule>("GET", `/api/calibration/${encodeURIComponent(rule)}`),
  setCalibrationThreshold: (rule: string, threshold: number | null, note?: string) =>
    request<CalibrationRule>(
      "POST",
      `/api/calibration/${encodeURIComponent(rule)}/threshold`,
      { threshold, note: note ?? null },
    ),

  listIncidents: (
    params: {
      namespace?: string;
      severity?: string;
      status?: string;
      since?: string;
      // Phase 14.5 — workload-drill-down filters.
      pod?: string;
      rule?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<IncidentList>("GET", `/api/incidents${suffix}`);
  },
  getIncident: (id: string) => request<IncidentDetail>("GET", `/api/incidents/${id}`),
  deleteIncident: (id: string) =>
    request<{ ok: boolean; deleted: string }>("DELETE", `/api/incidents/${id}`),
  bulkDeleteIncidents: (ids: string[], onlyHealed = false) =>
    request<{ ok: boolean; deleted: number; missing: number; requested: number }>(
      "POST",
      "/api/incidents/bulk-delete",
      { ids, only_healed: onlyHealed },
    ),

  listAudit: (params: { since?: string; limit?: number; incident_id?: string } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<AuditList>("GET", `/api/audit${suffix}`);
  },
  verifyAudit: () => request<{ valid: boolean }>("POST", "/api/audit/verify"),

  listScenarios: () => request<ScenarioDescriptor[]>("GET", "/api/scenarios"),
  runScenario: (scenario_id: number, overrides?: Record<string, unknown>) =>
    request<{ incident_id: string; subject: string }>("POST", "/api/scenarios/run", {
      scenario_id,
      overrides,
    }),

  listUseCases: () => request<UseCase[]>("GET", "/api/usecases"),
  createUseCase: (body: Record<string, unknown>) => request<UseCase>("POST", "/api/usecases", body),
  updateUseCase: (id: string, body: Record<string, unknown>) =>
    request<UseCase>("PUT", `/api/usecases/${id}`, body),
  deleteUseCase: (id: string) => request<{ deleted: boolean }>("DELETE", `/api/usecases/${id}`),
  runUseCase: (id: string) =>
    request<{ incident_id: string; use_case_id: string }>("POST", `/api/usecases/${id}/run`),

  // Phase 13 — baselines read-view. Optional ns/pod filter.
  listBaselines: (params: { ns?: string; pod?: string } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<BaselineList>("GET", `/api/baselines${suffix}`);
  },

  // Phase 14.5 — forecasts read-view for the workload drill-down +
  // pipeline scene pane. One row per (metric, namespace, scope)
  // known to the baseline store. `level=none` covers cold-start /
  // bad-fit keys; the SPA renders these dimmed.
  listForecasts: (params: { ns?: string; pod?: string } = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ForecastList>("GET", `/api/forecasts${suffix}`);
  },

  // Phase 14.5 — aggregated "scene" view of one incident. The
  // pipeline page's right-hand pane binds to this; the SPA also
  // polls it as the incident's graph progresses (the persisted
  // snapshot grows row-by-row as audit events land).
  getScene: (incidentId: string) =>
    request<WorkloadScene>("GET", `/api/scene/${encodeURIComponent(incidentId)}`),

  listApprovals: () => request<PendingApproval[]>("GET", "/api/approvals"),
  approveAction: (incident_id: string) =>
    request<{ approved: boolean; executed: boolean; action_result?: unknown; reason?: string }>(
      "POST",
      "/api/actions/approve",
      { incident_id },
    ),
  denyAction: (incident_id: string, reason: string) =>
    request<{ approved: boolean }>("POST", "/api/actions/deny", { incident_id, reason }),
};
