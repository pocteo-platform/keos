// Single source of truth for product naming, attribution, and brand tokens.
// Swap PRODUCT_NAME here and the SPA tab title, sidebar header, login page,
// and footer attribution all update in one place.
//
// Generic UI labels (the ones that used to name K8sGPT / Falco) live in the
// i18n JSON files — this file is for product-level branding only.

// Product hierarchy:
//   Pocteo Platform  → the company
//      └── Keos      → the Kubernetes edge OS (the product)
//             └── Keos SRE Agent  → the autonomous reliability layer
//                                    that ships inside every Keos edge
//                                    node (THIS app).
export const COMPANY_NAME = "Pocteo Platform";
export const PLATFORM_NAME = "Keos";
export const PRODUCT_NAME = "Keos SRE Agent";

export const TAGLINE_EN =
  "The autonomous SRE that lives inside every Keos edge node — by Pocteo Platform.";
export const TAGLINE_FR =
  "L'agent SRE autonome embarqué dans chaque nœud edge Keos — par Pocteo Platform.";

export const BRAND_FOOTER_EN = "Pocteo Platform · Keos edge OS";
export const BRAND_FOOTER_FR = "Pocteo Platform · Keos edge OS";

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

// ─── Security domain (Phase 21) ─────────────────────────────────────────
// The same 9 LangGraph nodes, re-framed for runtime security. The SRE loop
// is Observe→Predict→Heal→Learn; the security loop is
// Detect→Investigate→Contain→Respond. Node functions + audit event types
// are unchanged in the backend — this is a UI relabel keyed on the
// incident's `domain`.
export const SECURITY_LOOP_PHASES = ["detect", "investigate", "contain", "respond"] as const;
export type SecurityPhase = (typeof SECURITY_LOOP_PHASES)[number];

export const SECURITY_PHASE_BY_NODE: Record<string, SecurityPhase> = {
  triage: "detect",
  retrieve_context: "detect",
  k8sgpt_analyze: "detect",
  diagnose: "investigate",
  plan_action: "investigate",
  policy_gate: "contain",
  execute: "contain",
  verify: "respond",
  escalate: "respond",
};

// Security phases reuse the same Tailwind colour tokens, mapped so the
// "act" phase (contain) keeps the brand-primary emphasis.
export const SECURITY_PHASE_VARIANT: Record<SecurityPhase, "warn" | "info" | "primary" | "ok"> = {
  detect: "warn", // amber — a signal arrived
  investigate: "info", // cyan — reason about the threat
  contain: "primary", // brand — the containment action
  respond: "ok", // green — escalate + close
};

export type Domain = "sre" | "security";

// Domain-aware accessors. Components pass the incident/use-case domain and
// get back the right phase list / node-map / colour map / label key — so a
// single PipelineCanvas or Timeline renders both domains correctly.
export const phasesForDomain = (domain: Domain): readonly string[] =>
  domain === "security" ? SECURITY_LOOP_PHASES : LOOP_PHASES;

export const phaseByNodeForDomain = (domain: Domain): Record<string, string> =>
  domain === "security" ? SECURITY_PHASE_BY_NODE : PHASE_BY_NODE;

export const phaseVariantForDomain = (
  domain: Domain,
  phase: string,
): "warn" | "info" | "primary" | "ok" =>
  domain === "security"
    ? (SECURITY_PHASE_VARIANT[phase as SecurityPhase] ?? "info")
    : (PHASE_VARIANT[phase as LoopPhase] ?? "info");

// i18n label key for a phase chip, domain-aware. Security phases live under
// `pipeline.timeline.securityPhases.<phase>`.
export const phaseLabelKeyForDomain = (domain: Domain, phase: string): string =>
  domain === "security"
    ? `pipeline.timeline.securityPhases.${phase}`
    : `pipeline.timeline.phases.${phase}`;

// Domain badge display — i18n label key + a colour token.
export const DOMAIN_LABEL_KEY = (domain: Domain): string => `domain.${domain}`;
export const DOMAIN_VARIANT: Record<Domain, "info" | "danger"> = {
  sre: "info",
  security: "danger",
};
