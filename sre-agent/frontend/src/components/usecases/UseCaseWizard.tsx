import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Steps } from "@/components/ui/steps";
import { Step1Context, isStep1Valid } from "./Step1Context";
import { Step2Target, isStep2Valid } from "./Step2Target";
import { Step3FalcoTemplate, isStep3Valid } from "./Step3FalcoTemplate";
import { Step4Expected, isStep4Valid } from "./Step4Expected";
import { Step5Runbook, isStep5Valid } from "./Step5Runbook";
import { Step6Review } from "./Step6Review";
import { emptyDraft, type Draft } from "./types";
import { api } from "@/lib/api";

const VALIDATORS = [isStep1Valid, isStep2Valid, isStep3Valid, isStep4Valid, isStep5Valid];

interface Props {
  initial?: Draft;
  onClose: () => void;
}

export function UseCaseWizard({ initial, onClose }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState(1);
  const [draft, setDraftState] = useState<Draft>(initial ?? emptyDraft());

  const setDraft = (patch: Partial<Draft>) => setDraftState((d) => ({ ...d, ...patch }));

  const stepLabels = [
    t("usecases.wizard.steps.context"),
    t("usecases.wizard.steps.target"),
    t("usecases.wizard.steps.falco"),
    t("usecases.wizard.steps.expected"),
    t("usecases.wizard.steps.runbook"),
    t("usecases.wizard.steps.review"),
  ];

  // Mutations: create / update / run.
  const saveMut = useMutation({
    mutationFn: () =>
      draft.id
        ? api.updateUseCase(draft.id, draft as unknown as Record<string, unknown>)
        : api.createUseCase(draft as unknown as Record<string, unknown>),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["usecases"] });
    },
  });

  const saveAndRunMut = useMutation({
    mutationFn: async () => {
      const saved = draft.id
        ? await api.updateUseCase(draft.id, draft as unknown as Record<string, unknown>)
        : await api.createUseCase(draft as unknown as Record<string, unknown>);
      const run = await api.runUseCase(saved.id);
      return { saved, run };
    },
    onSuccess: ({ saved, run }) => {
      void qc.invalidateQueries({ queryKey: ["usecases"] });
      navigate(`/pipeline/${run.incident_id}?usecase=${saved.id}`);
    },
  });

  const canNext = step <= 5 ? VALIDATORS[step - 1](draft) : true;

  const onSave = () => saveMut.mutate(undefined, { onSuccess: () => onClose() });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">
          {draft.id ? t("usecases.wizard.titleEdit") : t("usecases.wizard.titleNew")}
        </CardTitle>
        <div className="mt-4">
          <Steps
            steps={stepLabels.map((label, i) => ({ id: i + 1, label }))}
            current={step}
            onStepClick={(id) => id < step && setStep(id)}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <div>
          {step === 1 && <Step1Context draft={draft} setDraft={setDraft} />}
          {step === 2 && <Step2Target draft={draft} setDraft={setDraft} />}
          {step === 3 && <Step3FalcoTemplate draft={draft} setDraft={setDraft} />}
          {step === 4 && <Step4Expected draft={draft} setDraft={setDraft} />}
          {step === 5 && <Step5Runbook draft={draft} setDraft={setDraft} />}
          {step === 6 && <Step6Review draft={draft} />}
        </div>

        <div className="flex items-center justify-between border-t pt-4">
          <Button variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <div className="flex gap-2">
            {step > 1 ? (
              <Button variant="outline" onClick={() => setStep(step - 1)}>
                {t("usecases.wizard.previous")}
              </Button>
            ) : null}
            {step < 6 ? (
              <Button onClick={() => setStep(step + 1)} disabled={!canNext}>
                {t("usecases.wizard.next")}
              </Button>
            ) : (
              <>
                <Button
                  variant="outline"
                  onClick={onSave}
                  disabled={saveMut.isPending || saveAndRunMut.isPending}
                >
                  {t("usecases.wizard.save")}
                </Button>
                <Button
                  onClick={() => saveAndRunMut.mutate()}
                  disabled={saveMut.isPending || saveAndRunMut.isPending}
                >
                  {t("usecases.wizard.saveAndRun")}
                </Button>
              </>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
