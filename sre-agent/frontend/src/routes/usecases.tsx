import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, Pencil, Copy, Trash2, Play } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, type UseCase } from "@/lib/api";
import { useApp } from "@/lib/store";
import { UseCaseWizard } from "@/components/usecases/UseCaseWizard";
import { emptyDraft, fromUseCase, type Draft } from "@/components/usecases/types";
import { Spinner } from "@/components/pipeline/RunningIndicator";

type WizardMode = { kind: "new" } | { kind: "edit"; draft: Draft } | null;

export default function UseCasesPage() {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [wizard, setWizard] = useState<WizardMode>(null);

  const q = useQuery({ queryKey: ["usecases"], queryFn: api.listUseCases });

  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteUseCase(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["usecases"] }),
  });
  const runMut = useMutation({
    mutationFn: (id: string) => api.runUseCase(id),
    onSuccess: ({ incident_id, use_case_id }) => {
      navigate(`/pipeline/${incident_id}?usecase=${use_case_id}`);
    },
  });

  if (wizard) {
    return (
      <UseCaseWizard
        initial={wizard.kind === "edit" ? wizard.draft : emptyDraft()}
        onClose={() => setWizard(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("usecases.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("usecases.subtitle")}</p>
        </div>
        <Button onClick={() => setWizard({ kind: "new" })}>
          <Plus className="h-4 w-4" />
          {t("usecases.new")}
        </Button>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : (q.data?.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {t("usecases.empty")}
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(q.data ?? []).map((uc) => (
            <UseCaseCard
              key={uc.id}
              uc={uc}
              locale={locale}
              isRunning={runMut.isPending && runMut.variables === uc.id}
              onRun={() => runMut.mutate(uc.id)}
              onEdit={() => setWizard({ kind: "edit", draft: fromUseCase(uc) })}
              onDuplicate={() => {
                const dup = fromUseCase(uc);
                dup.id = undefined;
                dup.name = `${uc.name} (copy)`;
                setWizard({ kind: "edit", draft: dup });
              }}
              onDelete={() => {
                if (window.confirm(t("usecases.confirmDelete", { name: uc.name }))) {
                  delMut.mutate(uc.id);
                }
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function UseCaseCard({
  uc,
  locale,
  isRunning,
  onRun,
  onEdit,
  onDuplicate,
  onDelete,
}: {
  uc: UseCase;
  locale: string;
  isRunning: boolean;
  onRun: () => void;
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const desc =
    locale === "fr"
      ? uc.description_fr || uc.description_en
      : uc.description_en || uc.description_fr;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{uc.name}</CardTitle>
        <CardDescription className="line-clamp-3">{desc}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex flex-wrap gap-1">
          {uc.tags.map((tag) => (
            <Badge key={tag} variant="secondary">
              {tag}
            </Badge>
          ))}
        </div>
        <div className="space-y-1 text-xs text-muted-foreground">
          <div>
            <span className="font-mono">{uc.target.namespace}</span> ·{" "}
            <span className="font-mono">
              {uc.target.pod_name ??
                Object.entries(uc.target.pod_label_selector ?? {})
                  .map(([k, v]) => `${k}=${v}`)
                  .join(",")}
            </span>
          </div>
          <div>
            {t("usecases.card.rule")}: <span className="font-mono">{uc.falco.rule}</span>
          </div>
          {uc.expectation.proposed_tool ? (
            <div>
              {t("usecases.card.expected")}:{" "}
              <Badge variant="info">{uc.expectation.proposed_tool}</Badge>
            </div>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="flex justify-end gap-1 border-t pt-3">
        <Button size="sm" variant="ghost" onClick={onDuplicate} aria-label={t("common.duplicate")}>
          <Copy className="h-4 w-4" />
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit} aria-label={t("common.edit")}>
          <Pencil className="h-4 w-4" />
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete} aria-label={t("common.delete")}>
          <Trash2 className="h-4 w-4 text-danger" />
        </Button>
        <Button size="sm" onClick={onRun} disabled={isRunning}>
          {isRunning ? <Spinner /> : <Play className="h-4 w-4" />}
          {isRunning ? t("common.running", "Running…") : t("common.run")}
        </Button>
      </CardFooter>
    </Card>
  );
}
