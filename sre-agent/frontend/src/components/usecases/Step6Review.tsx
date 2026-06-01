import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonBlock } from "@/components/ui/json-block";
import type { Draft } from "./types";

export function Step6Review({ draft }: { draft: Draft }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t("usecases.wizard.review.summary")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div>
            <span className="text-muted-foreground">name:</span>{" "}
            <span className="font-medium">{draft.name}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {draft.tags.map((tag) => (
              <Badge key={tag} variant="secondary">
                {tag}
              </Badge>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <Field label="namespace" value={draft.target.namespace} />
            <Field label="owner_kind" value={draft.target.owner_kind} />
            <Field
              label="pod"
              value={
                draft.target.pod_name ??
                Object.entries(draft.target.pod_label_selector ?? {})
                  .map(([k, v]) => `${k}=${v}`)
                  .join(",")
              }
            />
            <Field label="rule" value={draft.falco.rule} />
            <Field label="priority" value={draft.falco.priority} />
            <Field label="expected tool" value={draft.expectation.proposed_tool ?? "—"} />
            <Field label="expected outcome" value={draft.expectation.outcome ?? "—"} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t("usecases.wizard.review.raw")}</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonBlock data={draft} maxHeightRem={24} />
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-card/40 p-2">
      <div className="font-mono text-[10px] uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-xs">{value || "—"}</div>
    </div>
  );
}
