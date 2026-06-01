// Static topology mirroring agent/graph.py's StateGraph.
//
// LangGraph 0.2 doesn't expose its graph to JS; we mirror the topology here
// and rely on the trace events to tell us which conditional edge fired.
// Keep this file in lockstep with agent/graph.py:build_graph().

export type NodeKind =
  | "triage"
  | "retrieve_context"
  | "k8sgpt_analyze"
  | "diagnose"
  | "plan_action"
  | "policy_gate"
  | "execute"
  | "verify"
  | "escalate";

export interface PipelineEdgeSpec {
  id: string;
  source: NodeKind;
  target: NodeKind;
  /** If true, the edge is only traversed on a specific routing outcome
   *  (low confidence, deny, fail). Drawn dashed-amber. */
  conditional?: boolean;
}

export interface PipelineNodeSpec {
  id: NodeKind;
  position: { x: number; y: number };
  /** Lucide-react icon name (resolved at render). */
  icon: string;
  /** i18n key under `pipeline.nodes.<id>.{title,desc}`. */
  i18nKey: NodeKind;
}

// Layout: a single column for the happy path, with `escalate` to the right
// at the same height as plan_action so the escalation edges all converge
// nicely.
const STEP_DY = 110;
const HAPPY_X = 280;
const ESCALATE_X = 640;

export const TOPOLOGY_NODES: PipelineNodeSpec[] = [
  { id: "triage", position: { x: HAPPY_X, y: 0 * STEP_DY }, icon: "Inbox", i18nKey: "triage" },
  {
    id: "retrieve_context",
    position: { x: HAPPY_X, y: 1 * STEP_DY },
    icon: "Search",
    i18nKey: "retrieve_context",
  },
  {
    id: "k8sgpt_analyze",
    position: { x: HAPPY_X, y: 2 * STEP_DY },
    icon: "Sparkles",
    i18nKey: "k8sgpt_analyze",
  },
  { id: "diagnose", position: { x: HAPPY_X, y: 3 * STEP_DY }, icon: "Brain", i18nKey: "diagnose" },
  {
    id: "plan_action",
    position: { x: HAPPY_X, y: 4 * STEP_DY },
    icon: "Wrench",
    i18nKey: "plan_action",
  },
  {
    id: "policy_gate",
    position: { x: HAPPY_X, y: 5 * STEP_DY },
    icon: "ShieldCheck",
    i18nKey: "policy_gate",
  },
  { id: "execute", position: { x: HAPPY_X, y: 6 * STEP_DY }, icon: "Play", i18nKey: "execute" },
  {
    id: "verify",
    position: { x: HAPPY_X, y: 7 * STEP_DY },
    icon: "CheckCircle",
    i18nKey: "verify",
  },
  {
    id: "escalate",
    position: { x: ESCALATE_X, y: 4 * STEP_DY },
    icon: "Siren",
    i18nKey: "escalate",
  },
];

export const TOPOLOGY_EDGES: PipelineEdgeSpec[] = [
  { id: "triage-retrieve", source: "triage", target: "retrieve_context" },
  { id: "retrieve-k8sgpt", source: "retrieve_context", target: "k8sgpt_analyze" },
  { id: "k8sgpt-diagnose", source: "k8sgpt_analyze", target: "diagnose" },
  { id: "diagnose-plan", source: "diagnose", target: "plan_action" },
  { id: "diagnose-escalate", source: "diagnose", target: "escalate", conditional: true },
  { id: "plan-policy", source: "plan_action", target: "policy_gate" },
  { id: "policy-execute", source: "policy_gate", target: "execute" },
  { id: "policy-escalate", source: "policy_gate", target: "escalate", conditional: true },
  { id: "execute-verify", source: "execute", target: "verify" },
  { id: "execute-escalate", source: "execute", target: "escalate", conditional: true },
  { id: "verify-escalate", source: "verify", target: "escalate", conditional: true },
];

/** All node IDs in the canonical happy-path order. */
export const NODE_ORDER: NodeKind[] = TOPOLOGY_NODES.map((n) => n.id);
