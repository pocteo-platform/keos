// Draft = form-state shape (everything mutable, validated by each step).
// Mirrors the UseCase shape minus server-controlled fields (id, timestamps).

import type { UseCase } from "@/lib/api";

export interface Draft {
  id?: string; // present in edit mode
  // Phase 21 — which agent domain this scenario drives. "security" publishes
  // to security.events.* and offers the security tool catalog in Step 4.
  domain: "sre" | "security";
  name: string;
  description_en: string;
  description_fr: string;
  tags: string[];
  target: UseCase["target"];
  falco: UseCase["falco"];
  expectation: UseCase["expectation"];
  // Passthrough so editing a security library case doesn't drop its
  // attack-simulation spec (there's no full editor UI for it yet).
  attack_sim?: UseCase["attack_sim"];
  runbook_en: string;
  runbook_fr: string;
}

export const emptyDraft = (): Draft => ({
  domain: "sre",
  name: "",
  description_en: "",
  description_fr: "",
  tags: [],
  target: {
    namespace: "workloads",
    owner_kind: "Deployment",
    pod_label_selector: { app: "nginx-demo" },
  },
  falco: { rule: "", priority: "Warning", output_template: "", output_fields: {} },
  expectation: { diagnosis_keywords: [], outcome: null, policy_allow: null, proposed_tool: null },
  runbook_en: "",
  runbook_fr: "",
});

export const fromUseCase = (uc: UseCase): Draft => ({
  id: uc.id,
  domain: uc.domain === "security" ? "security" : "sre",
  name: uc.name,
  description_en: uc.description_en,
  description_fr: uc.description_fr,
  tags: [...uc.tags],
  target: { ...uc.target },
  falco: { ...uc.falco, output_fields: { ...uc.falco.output_fields } },
  expectation: { ...uc.expectation, diagnosis_keywords: [...uc.expectation.diagnosis_keywords] },
  attack_sim: uc.attack_sim,
  runbook_en: uc.runbook_en,
  runbook_fr: uc.runbook_fr,
});
