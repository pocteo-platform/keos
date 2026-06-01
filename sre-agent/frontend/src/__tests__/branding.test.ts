// Consistency tests for the brand / phase tokens. These don't change often,
// but a bad refactor (rename a phase, drop a node from PHASE_BY_NODE) would
// break the UI silently — chips disappear, no error in dev tools. These
// tests are the trip wire.

import { describe, expect, it } from "vitest";

import {
  BRAND_FOOTER_EN,
  BRAND_FOOTER_FR,
  LOOP_PHASES,
  PHASE_BY_NODE,
  PHASE_LABEL_KEY,
  PHASE_VARIANT,
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
  type LoopPhase,
} from "@/lib/branding";

describe("branding constants", () => {
  it("PRODUCT_NAME is non-empty (UI tab title relies on it)", () => {
    expect(PRODUCT_NAME).toBeTruthy();
    expect(PRODUCT_NAME.length).toBeGreaterThan(2);
  });

  it("both taglines + footers are populated", () => {
    expect(TAGLINE_EN).toBeTruthy();
    expect(TAGLINE_FR).toBeTruthy();
    expect(BRAND_FOOTER_EN).toBeTruthy();
    expect(BRAND_FOOTER_FR).toBeTruthy();
  });

  it("LOOP_PHASES is the canonical 4-phase loop in order", () => {
    expect(LOOP_PHASES).toEqual(["observe", "predict", "heal", "learn"]);
  });
});

describe("PHASE_VARIANT", () => {
  it("declares a variant for every phase, no extras", () => {
    const phaseKeys = Object.keys(PHASE_VARIANT).sort();
    expect(phaseKeys).toEqual([...LOOP_PHASES].sort());
  });

  it("every variant is one of the allowed Tailwind tokens", () => {
    const allowed = new Set(["warn", "info", "primary", "ok"]);
    for (const v of Object.values(PHASE_VARIANT)) {
      expect(allowed.has(v)).toBe(true);
    }
  });
});

describe("PHASE_BY_NODE", () => {
  it("maps every documented graph node to a valid phase", () => {
    // If a new node lands in agent/graph.py, add it here AND to
    // PHASE_BY_NODE — this assertion catches the "added in backend,
    // forgot the frontend mapping" case.
    const expectedNodes = [
      "triage",
      "retrieve_context",
      "k8sgpt_analyze",
      "diagnose",
      "plan_action",
      "policy_gate",
      "execute",
      "verify",
      "escalate",
    ];
    expect(Object.keys(PHASE_BY_NODE).sort()).toEqual(expectedNodes.sort());

    const validPhases: ReadonlySet<LoopPhase> = new Set(LOOP_PHASES);
    for (const [node, phase] of Object.entries(PHASE_BY_NODE)) {
      expect(validPhases.has(phase), `node ${node} → invalid phase ${phase}`).toBe(true);
    }
  });

  it("each phase is reachable from at least one node (no orphan chips)", () => {
    const seen = new Set(Object.values(PHASE_BY_NODE));
    for (const phase of LOOP_PHASES) {
      expect(seen.has(phase), `phase ${phase} has no nodes mapped to it`).toBe(true);
    }
  });
});

describe("PHASE_LABEL_KEY", () => {
  it("produces a stable i18n key shape for every phase", () => {
    for (const phase of LOOP_PHASES) {
      expect(PHASE_LABEL_KEY(phase)).toBe(`pipeline.timeline.phases.${phase}`);
    }
  });
});
