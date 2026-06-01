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

// ---------- endpoint helpers ----------

export const api = {
  health: () => request<HealthResponse>("GET", "/api/health"),
  getMode: () => request<ModeResponse>("GET", "/api/mode"),
  setMode: (mode: AgentMode, note?: string) =>
    request<ModeResponse>("POST", "/api/mode", { mode, note }),

  listNodes: () => request<NodeSummary[]>("GET", "/api/cluster/nodes"),
  listNamespaces: () => request<NamespaceSummary[]>("GET", "/api/cluster/namespaces"),
  listPods: (ns: string) =>
    request<PodSummary[]>("GET", `/api/cluster/pods?ns=${encodeURIComponent(ns)}`),
  listDeployments: (ns: string) =>
    request<DeploymentSummary[]>("GET", `/api/cluster/deployments?ns=${encodeURIComponent(ns)}`),
  listEvents: (ns: string, limit = 50) =>
    request<EventSummary[]>(
      "GET",
      `/api/cluster/events?ns=${encodeURIComponent(ns)}&limit=${limit}`,
    ),
  listActiveIncidents: () => request<ActiveIncident[]>("GET", "/api/incidents/active"),

  listIncidents: (
    params: {
      namespace?: string;
      severity?: string;
      status?: string;
      since?: string;
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
