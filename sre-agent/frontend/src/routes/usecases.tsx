import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, Pencil, Copy, Trash2, Play, Search, X } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { api, type UseCase } from "@/lib/api";
import { useApp } from "@/lib/store";
import { UseCaseWizard } from "@/components/usecases/UseCaseWizard";
import { emptyDraft, fromUseCase, type Draft } from "@/components/usecases/types";
import { Spinner } from "@/components/pipeline/RunningIndicator";
import { DOMAIN_VARIANT, type Domain } from "@/lib/branding";

type WizardMode = { kind: "new" } | { kind: "edit"; draft: Draft } | null;
type DomainFilter = "all" | "sre" | "security";

export default function UseCasesPage() {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [wizard, setWizard] = useState<WizardMode>(null);
  const [filter, setFilter] = useState("");
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [domainFilter, setDomainFilter] = useState<DomainFilter>("all");

  const q = useQuery({ queryKey: ["usecases"], queryFn: api.listUseCases });

  // Aggregate every tag across all use cases so the chip row stays
  // stable as the user types — no flicker when the list narrows.
  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const uc of q.data ?? []) for (const tag of uc.tags) s.add(tag);
    return [...s].sort();
  }, [q.data]);

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (q.data ?? []).filter((uc) => {
      const dom: Domain = uc.domain === "security" ? "security" : "sre";
      if (domainFilter !== "all" && dom !== domainFilter) return false;
      if (activeTag && !uc.tags.includes(activeTag)) return false;
      if (!needle) return true;
      const desc = `${uc.description_en} ${uc.description_fr}`.toLowerCase();
      return (
        uc.name.toLowerCase().includes(needle) ||
        desc.includes(needle) ||
        uc.target.namespace.toLowerCase().includes(needle) ||
        uc.falco.rule.toLowerCase().includes(needle)
      );
    });
  }, [q.data, filter, activeTag, domainFilter]);

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

      {/* Search + tag filter chips */}
      {(q.data?.length ?? 0) > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative max-w-sm flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("usecases.search", {
                defaultValue: "Search by name, description, rule, namespace…",
              })}
              className="h-9 pl-8"
            />
            {filter ? (
              <button
                type="button"
                onClick={() => setFilter("")}
                aria-label={t("common.clear", { defaultValue: "Clear" })}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
          {/* Domain toggle: All / SRE / Security (Phase 21) */}
          <div className="flex items-center gap-1">
            {(["all", "sre", "security"] as const).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDomainFilter(d)}
                className="rounded-none"
              >
                <Badge
                  variant={
                    domainFilter === d ? (d === "security" ? "danger" : "info") : "secondary"
                  }
                  className={cn(
                    "cursor-pointer text-[10px] uppercase tracking-wider",
                    domainFilter === d && "ring-1 ring-current",
                  )}
                >
                  {d === "all" ? t("domain.all", { defaultValue: "All" }) : t(`domain.${d}`)}
                </Badge>
              </button>
            ))}
          </div>
          {allTags.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1">
              {allTags.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setActiveTag((cur) => (cur === tag ? null : tag))}
                  className="rounded-none"
                >
                  <Badge
                    variant={activeTag === tag ? "info" : "secondary"}
                    className={cn(
                      "cursor-pointer text-[10px] uppercase tracking-wider",
                      activeTag === tag && "ring-1 ring-info",
                    )}
                  >
                    {tag}
                  </Badge>
                </button>
              ))}
              {activeTag ? (
                <button
                  type="button"
                  onClick={() => setActiveTag(null)}
                  className="text-[10px] text-muted-foreground hover:text-foreground"
                >
                  {t("common.clear", { defaultValue: "Clear" })}
                </button>
              ) : null}
            </div>
          ) : null}
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            {filtered.length} / {q.data?.length ?? 0}
          </span>
        </div>
      ) : null}

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : (q.data?.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {t("usecases.empty")}
          </CardContent>
        </Card>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent className="space-y-2 py-12 text-center text-sm text-muted-foreground">
            <p>
              {t("usecases.noMatch", {
                defaultValue: "No use cases match your filters.",
              })}
            </p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setFilter("");
                setActiveTag(null);
              }}
            >
              {t("common.clearFilters", { defaultValue: "Clear filters" })}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((uc) => (
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
  const dom: Domain = uc.domain === "security" ? "security" : "sre";
  return (
    <Card className={cn(dom === "security" && "border-danger/40")}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-base">{uc.name}</CardTitle>
          <Badge variant={DOMAIN_VARIANT[dom]} className="shrink-0 text-[10px] uppercase">
            {t(`domain.${dom}`)}
          </Badge>
        </div>
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
