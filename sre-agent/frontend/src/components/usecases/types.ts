// Draft = form-state shape (everything mutable, validated by each step).
// Mirrors the UseCase shape minus server-controlled fields (id, timestamps).

import type { UseCase } from "@/lib/api";

export interface Draft {
  id?: string; // present in edit mode
  name: string;
  description_en: string;
  description_fr: string;
  tags: string[];
  target: UseCase["target"];
  falco: UseCase["falco"];
  expectation: UseCase["expectation"];
  runbook_en: string;
  runbook_fr: string;
}

export const emptyDraft = (): Draft => ({
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
  name: uc.name,
  description_en: uc.description_en,
  description_fr: uc.description_fr,
  tags: [...uc.tags],
  target: { ...uc.target },
  falco: { ...uc.falco, output_fields: { ...uc.falco.output_fields } },
  expectation: { ...uc.expectation, diagnosis_keywords: [...uc.expectation.diagnosis_keywords] },
  runbook_en: uc.runbook_en,
  runbook_fr: uc.runbook_fr,
});
