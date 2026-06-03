/**
 * Per-step validity predicates for the Use Case Wizard. Extracted
 * into their own module so each `StepN.tsx` file can stay
 * components-only (React Fast Refresh requires this).
 *
 * The wizard imports `VALIDATORS` (the array form, indexed by step
 * number minus one) directly; the individual `isStepNValid`
 * functions stay exported for granular usage / unit tests if
 * anyone needs them later.
 */

import type { Draft } from "./types";

export const isStep1Valid = (d: Draft) =>
  d.name.trim().length > 0 &&
  (d.description_en.trim().length > 0 || d.description_fr.trim().length > 0);

export const isStep2Valid = (d: Draft) => {
  if (!d.target.namespace) return false;
  if (d.target.pod_name) return true;
  return Object.keys(d.target.pod_label_selector ?? {}).length > 0;
};

export const isStep3Valid = (d: Draft) =>
  d.falco.rule.trim().length > 0 && d.falco.output_template.trim().length > 0;

// Step 4 (expectations) and Step 5 (runbook) are optional — no validation.
// The `_d` underscore-prefix tells the lint rule the param is intentionally
// unused; keeps the signature uniform with the other validators.
export const isStep4Valid = (_d: Draft) => true;
export const isStep5Valid = (_d: Draft) => true;

/** Indexed array form used by the wizard's step-index machinery. */
export const VALIDATORS: ReadonlyArray<(d: Draft) => boolean> = [
  isStep1Valid,
  isStep2Valid,
  isStep3Valid,
  isStep4Valid,
  isStep5Valid,
];
