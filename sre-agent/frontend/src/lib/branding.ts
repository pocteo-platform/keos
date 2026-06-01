// Single source of truth for product naming, attribution, and brand tokens.
// Swap PRODUCT_NAME here and the SPA tab title, sidebar header, login page,
// and footer attribution all update in one place.
//
// Generic UI labels (the ones that used to name K8sGPT / Falco) live in the
// i18n JSON files — this file is for product-level branding only.

export const PRODUCT_NAME = "Keos SRE Agent";

export const TAGLINE_EN = "The Autonomous AI SRE Agent for Your Cloud-Native Infrastructure";
export const TAGLINE_FR =
  "L'agent SRE autonome propulsé par l'IA pour vos infrastructures cloud-native";

export const BRAND_FOOTER_EN = "Created by Keos platform";
export const BRAND_FOOTER_FR = "Créé par la plateforme Keos";

// The agentic loop. Renders as phase chips on the timeline + as lane
// dividers / colors on the React Flow graph.
export const LOOP_PHASES = ["observe", "predict", "heal", "learn"] as const;
export type LoopPhase = (typeof LOOP_PHASES)[number];

// Maps each LangGraph node (kept as-is in the backend) to one of the four
// product-facing phases. Single source — consumed by IncidentTimeline,
// TelemetrySidebar, and PipelineCanvas alike.
export const PHASE_BY_NODE: Record<string, LoopPhase> = {
  triage: "observe",
  retrieve_context: "observe",
  k8sgpt_analyze: "observe",
  diagnose: "predict",
  plan_action: "predict",
  policy_gate: "heal",
  execute: "heal",
  verify: "learn",
  escalate: "learn",
};

// Stable colour token name per phase (mapped to the Tailwind domain colours
// defined in tailwind.config.ts: ok / warn / danger / info).
export const PHASE_VARIANT: Record<LoopPhase, "warn" | "info" | "primary" | "ok"> = {
  observe: "warn", // amber — "watch / listen"
  predict: "info", // cyan/blue — "reason"
  heal: "primary", // brand blue — "act"
  learn: "ok", // green — "consolidate"
};

// i18n keys for the phase chip labels. Translations live in en.json/fr.json
// under `pipeline.timeline.phases.<phase>`.
export const PHASE_LABEL_KEY = (phase: LoopPhase): string => `pipeline.timeline.phases.${phase}`;
