// Single source for the LangGraph-node → loop-phase mapping used by both
// the timeline view (lane assignment) and the React Flow canvas (node
// colour). The mapping itself lives in `lib/branding.ts` so swapping the
// loop terminology is a one-file change.

import { PHASE_BY_NODE, PHASE_VARIANT, type LoopPhase } from "@/lib/branding";
import type { NodeKind } from "@/lib/pipeline-topology";

export function phaseOfNode(kind: NodeKind): LoopPhase {
  // PHASE_BY_NODE is keyed by string; we assert that every NodeKind is a
  // valid key at compile time elsewhere — fall back to "learn" for safety.
  return (PHASE_BY_NODE[kind] ?? "learn") as LoopPhase;
}

export function variantOfPhase(phase: LoopPhase): "warn" | "info" | "primary" | "ok" {
  return PHASE_VARIANT[phase];
}

export type { LoopPhase };
