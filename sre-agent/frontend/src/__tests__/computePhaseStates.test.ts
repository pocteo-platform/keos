// Pure-function tests for the RunningIndicator's phase reducer.
//
// `computePhaseStates` is the small piece of logic that converts a sequence
// of WebSocket trace events into the 4-chip state used by the loader. It's
// the only piece of the loader that isn't trivially visual, so it gets
// guard-rail tests.

import { describe, expect, it } from "vitest";

import { computePhaseStates, type PhaseState } from "@/components/pipeline/computePhaseStates";
import type { LoopPhase } from "@/lib/branding";

// Helper: assert that the returned shape covers exactly the 4 phases.
function expectAllPhases(states: Record<LoopPhase, PhaseState>) {
  expect(Object.keys(states).sort()).toEqual(["heal", "learn", "observe", "predict"]);
}

describe("computePhaseStates", () => {
  it("returns all-pending when no events seen", () => {
    const states = computePhaseStates([]);
    expectAllPhases(states);
    expect(states.observe).toBe("pending");
    expect(states.predict).toBe("pending");
    expect(states.heal).toBe("pending");
    expect(states.learn).toBe("pending");
  });

  it("marks observe active on triage.start", () => {
    const states = computePhaseStates([{ kind: "triage", phase: "start" }]);
    expect(states.observe).toBe("active");
    expect(states.predict).toBe("pending");
    expect(states.heal).toBe("pending");
    expect(states.learn).toBe("pending");
  });

  it("marks observe done when its node ends", () => {
    const states = computePhaseStates([
      { kind: "triage", phase: "start" },
      { kind: "triage", phase: "end" },
    ]);
    expect(states.observe).toBe("done");
    expect(states.predict).toBe("pending");
  });

  it("predict.start retroactively closes observe even without its end event", () => {
    // Real pipelines fire end-of-observe then start-of-predict in order, but
    // a network blip could drop the end frame. The reducer's job is to keep
    // the UI honest: if a later phase has started, all earlier phases are
    // by definition complete.
    const states = computePhaseStates([
      { kind: "triage", phase: "start" },
      { kind: "diagnose", phase: "start" },
    ]);
    expect(states.observe).toBe("done");
    expect(states.predict).toBe("active");
  });

  it("walks the full Observe → Predict → Heal → Learn progression", () => {
    const events: { kind: string; phase: "start" | "end" }[] = [
      { kind: "triage", phase: "start" },
      { kind: "triage", phase: "end" },
      { kind: "retrieve_context", phase: "start" },
      { kind: "retrieve_context", phase: "end" },
      { kind: "k8sgpt_analyze", phase: "start" },
      { kind: "k8sgpt_analyze", phase: "end" },
      { kind: "diagnose", phase: "start" },
      { kind: "diagnose", phase: "end" },
      { kind: "plan_action", phase: "start" },
      { kind: "plan_action", phase: "end" },
      { kind: "policy_gate", phase: "start" },
      { kind: "policy_gate", phase: "end" },
      { kind: "execute", phase: "start" },
      { kind: "execute", phase: "end" },
      { kind: "verify", phase: "start" },
      { kind: "verify", phase: "end" },
    ];
    const states = computePhaseStates(events);
    expect(states.observe).toBe("done");
    expect(states.predict).toBe("done");
    expect(states.heal).toBe("done");
    expect(states.learn).toBe("done");
  });

  it("ignores unknown node kinds (forward-compat for new graph nodes)", () => {
    const states = computePhaseStates([
      { kind: "triage", phase: "start" },
      { kind: "some_future_node", phase: "start" }, // not in PHASE_BY_NODE
    ]);
    // observe stays active; the unknown node doesn't promote anything.
    expect(states.observe).toBe("active");
    expect(states.predict).toBe("pending");
  });

  it("escalate (a learn-phase node) marks learn active", () => {
    const states = computePhaseStates([
      { kind: "triage", phase: "start" },
      { kind: "triage", phase: "end" },
      { kind: "escalate", phase: "start" },
    ]);
    expect(states.observe).toBe("done");
    expect(states.predict).toBe("done");
    expect(states.heal).toBe("done");
    expect(states.learn).toBe("active");
  });
});
