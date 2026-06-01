// Reducer for pipeline trace events.
//
// Each event is one of:
//   {phase: "start", node, incident_id, input_snapshot}
//   {phase: "end",   node, incident_id, elapsed_ms, output_snapshot, error?}
//
// We don't trust event ordering: a fast graph can race start/end across
// the WS, so we always merge into the prior state rather than reset.

import { useCallback, useMemo, useReducer } from "react";
import type { NodeKind } from "@/lib/pipeline-topology";

export type NodeStatus = "pending" | "active" | "done" | "error" | "skipped";

export interface NodeState {
  status: NodeStatus;
  startedAt?: number; // ms since first event
  endedAt?: number;
  elapsedMs?: number;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string | null;
}

export interface PipelineState {
  /** incident_id this state represents (null until first event arrives). */
  incidentId: string | null;
  /** First-event wall clock — anchors `startedAt`/`endedAt` to a 0-based timeline. */
  t0: number | null;
  nodes: Record<NodeKind, NodeState>;
  /** Edge IDs that have fired (helps the canvas draw the route). */
  firedEdges: Set<string>;
}

export interface TraceEvent {
  phase: "start" | "end";
  node: NodeKind;
  incident_id?: string;
  elapsed_ms?: number;
  input_snapshot?: Record<string, unknown>;
  output_snapshot?: Record<string, unknown>;
  error?: string | null;
  /** From the wildcard pipeline.* channel — included on every event the bus
   *  publishes, so the UI can demux when in /pipeline/live mode. */
  _channel?: string;
  ts?: string;
}

const EMPTY_NODES: PipelineState["nodes"] = {
  triage: { status: "pending" },
  retrieve_context: { status: "pending" },
  k8sgpt_analyze: { status: "pending" },
  diagnose: { status: "pending" },
  plan_action: { status: "pending" },
  policy_gate: { status: "pending" },
  execute: { status: "pending" },
  verify: { status: "pending" },
  escalate: { status: "pending" },
};

const initialState = (): PipelineState => ({
  incidentId: null,
  t0: null,
  nodes: { ...EMPTY_NODES },
  firedEdges: new Set(),
});

type Action = { type: "event"; event: TraceEvent } | { type: "reset" };

const tsToMs = (ts?: string): number => (ts ? new Date(ts).getTime() : Date.now());

function reduce(state: PipelineState, action: Action): PipelineState {
  if (action.type === "reset") return initialState();

  const ev = action.event;
  const wallMs = tsToMs(ev.ts);
  const t0 = state.t0 ?? wallMs;
  const incidentId = state.incidentId ?? ev.incident_id ?? null;
  const prev = state.nodes[ev.node] ?? { status: "pending" };

  const next: NodeState = { ...prev };

  if (ev.phase === "start") {
    next.status = "active";
    next.startedAt = wallMs - t0;
    next.input = ev.input_snapshot ?? next.input;
    next.error = null;
  } else {
    next.endedAt = wallMs - t0;
    next.elapsedMs =
      ev.elapsed_ms ?? (next.startedAt != null ? next.endedAt - next.startedAt : undefined);
    next.output = ev.output_snapshot ?? next.output;
    if (ev.error) {
      next.status = "error";
      next.error = ev.error;
    } else {
      next.status = "done";
    }
  }

  // Walk firedEdges by looking at output_snapshot when an `end` arrives.
  const firedEdges = new Set(state.firedEdges);
  if (ev.phase === "end") {
    // Conditional edges: pick the one that matches the routing decision.
    const out = (ev.output_snapshot ?? {}) as Record<string, unknown>;
    if (ev.node === "diagnose") {
      const conf =
        ((out.diagnosis as { confidence?: number } | undefined) ?? { confidence: 1 }).confidence ??
        1;
      firedEdges.add(conf < 0.6 ? "diagnose-escalate" : "diagnose-plan");
    } else if (ev.node === "policy_gate") {
      const allow = ((out.policy_decision as { allow?: boolean } | undefined) ?? {}).allow;
      firedEdges.add(allow === false ? "policy-escalate" : "policy-execute");
    } else if (ev.node === "execute") {
      const success = ((out.action_result as { success?: boolean } | undefined) ?? {}).success;
      firedEdges.add(success === false ? "execute-escalate" : "execute-verify");
    } else if (ev.node === "verify") {
      const healed = ((out.verification as { healed?: boolean } | undefined) ?? {}).healed;
      firedEdges.add(healed === false ? "verify-escalate" : "");
    } else {
      // unconditional edges
      const map: Record<NodeKind, string | undefined> = {
        triage: "triage-retrieve",
        retrieve_context: "retrieve-k8sgpt",
        k8sgpt_analyze: "k8sgpt-diagnose",
        plan_action: "plan-policy",
        diagnose: undefined,
        policy_gate: undefined,
        execute: undefined,
        verify: undefined,
        escalate: undefined,
      };
      const id = map[ev.node];
      if (id) firedEdges.add(id);
    }
  }

  return {
    incidentId,
    t0,
    nodes: { ...state.nodes, [ev.node]: next },
    firedEdges,
  };
}

export function usePipelineState() {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const apply = useCallback((event: TraceEvent) => dispatch({ type: "event", event }), []);
  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  // Derived: total elapsed of the slowest-ending node.
  const totalElapsedMs = useMemo(() => {
    let m = 0;
    for (const k of Object.keys(state.nodes) as NodeKind[]) {
      const v = state.nodes[k];
      if (v.endedAt != null && v.endedAt > m) m = v.endedAt;
    }
    return m;
  }, [state.nodes]);

  return { state, apply, reset, totalElapsedMs };
}
