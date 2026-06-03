/**
 * Phase-state reducer for the pipeline canvas. Lives in its own
 * non-component module so RunningIndicator.tsx stays components-only
 * (React Fast Refresh requirement).
 *
 * Walks a sequence of `(node, start|end)` events from the WS trace
 * stream and returns a per-phase state record. The rules:
 *
 *   - On `start`, mark that node's phase as `active` if currently `pending`.
 *   - On `end`,   mark that node's phase as `done`.
 *   - Any phase strictly before the latest-started phase is also `done`
 *     (the agent can't be in a later phase without having finished the
 *     earlier ones).
 *
 * Pure function — exhaustively unit-tested in
 * `src/__tests__/computePhaseStates.test.ts`.
 */
import { LOOP_PHASES, PHASE_BY_NODE, type LoopPhase } from "@/lib/branding";

export type PhaseState = "pending" | "active" | "done";

export function computePhaseStates(
  seenNodes: { kind: string; phase: "start" | "end" }[],
): Record<LoopPhase, PhaseState> {
  const phaseOrder: LoopPhase[] = [...LOOP_PHASES];
  const out: Record<LoopPhase, PhaseState> = {
    observe: "pending",
    predict: "pending",
    heal: "pending",
    learn: "pending",
  };
  let maxStartedIdx = -1;
  for (const ev of seenNodes) {
    const phase = PHASE_BY_NODE[ev.kind];
    if (!phase) continue;
    const idx = phaseOrder.indexOf(phase);
    if (idx < 0) continue;
    if (ev.phase === "start") {
      maxStartedIdx = Math.max(maxStartedIdx, idx);
      if (out[phase] === "pending") out[phase] = "active";
    } else {
      out[phase] = "done";
    }
  }
  // Phases strictly before the latest-started phase are done by inference.
  for (let i = 0; i < maxStartedIdx; i++) {
    if (out[phaseOrder[i]] === "active" || out[phaseOrder[i]] === "pending") {
      out[phaseOrder[i]] = "done";
    }
  }
  return out;
}
