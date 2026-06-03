import { useTranslation } from "react-i18next";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import type { Draft } from "./types";

interface Props {
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
}

export function Step5Runbook({ draft, setDraft }: Props) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-6 text-xs text-muted-foreground">
          {t("usecases.wizard.runbook.description")}
        </CardContent>
      </Card>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="rb-en">{t("usecases.wizard.runbook.en")}</Label>
          <Textarea
            id="rb-en"
            rows={14}
            value={draft.runbook_en}
            onChange={(e) => setDraft({ runbook_en: e.target.value })}
            className="font-mono text-xs"
            placeholder={t("usecases.wizard.runbook.placeholderEn")}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="rb-fr">{t("usecases.wizard.runbook.fr")}</Label>
          <Textarea
            id="rb-fr"
            rows={14}
            value={draft.runbook_fr}
            onChange={(e) => setDraft({ runbook_fr: e.target.value })}
            className="font-mono text-xs"
            placeholder={t("usecases.wizard.runbook.placeholderFr")}
          />
        </div>
      </div>
    </div>
  );
}

// Validator moved to `./step-validators.ts` (always-true: optional step).
