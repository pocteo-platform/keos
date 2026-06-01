import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { enUS, fr as frLocale } from "date-fns/locale";
import { ArrowUpDown, ChevronDown, ChevronUp, PlayCircle, Trash2 } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ModeBadge } from "@/components/kube/ModeBadge";
import { SeverityChip } from "./SeverityChip";
import { OutcomeBadge } from "./OutcomeBadge";
import { useApp } from "@/lib/store";
import { api } from "@/lib/api";
import type { IncidentSummary } from "@/lib/api";
import { Spinner } from "@/components/pipeline/RunningIndicator";

type SortKey = "ts" | "severity" | "namespace";
type SortDir = "asc" | "desc";

const PAGE = 50;

const SEVERITIES: IncidentSummary["severity"][] = ["low", "medium", "high", "critical"];

export function IncidentTable({ items }: { items: IncidentSummary[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const locale = useApp((s) => s.locale);
  const dfLocale = locale === "fr" ? frLocale : enUS;

  const bulkDelete = useMutation({
    mutationFn: (vars: { ids: string[]; onlyHealed?: boolean }) =>
      api.bulkDeleteIncidents(vars.ids, vars.onlyHealed),
    onSuccess: () => {
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ["incidents"] });
    },
  });

  const singleDelete = useMutation({
    mutationFn: (id: string) => api.deleteIncident(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["incidents"] });
    },
  });

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const q = params.get("q") ?? "";
  const severity = (params.get("severity") ?? "all") as IncidentSummary["severity"] | "all";
  const namespace = params.get("namespace") ?? "all";
  const status = params.get("status") ?? "all";
  const sortKey = (params.get("sort") ?? "ts") as SortKey;
  const sortDir = (params.get("dir") ?? "desc") as SortDir;

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "" || v === "all") next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
    setOffset(0);
  };

  const namespaces = useMemo(() => {
    const s = new Set<string>();
    for (const i of items) if (i.namespace) s.add(i.namespace);
    return Array.from(s).sort();
  }, [items]);

  const filtered = useMemo(() => {
    let out = items;
    if (q) {
      const needle = q.toLowerCase();
      out = out.filter(
        (i) =>
          i.incident_id.toLowerCase().includes(needle) ||
          (i.rule ?? "").toLowerCase().includes(needle) ||
          (i.pod ?? "").toLowerCase().includes(needle) ||
          (i.diagnosis_summary ?? "").toLowerCase().includes(needle),
      );
    }
    if (severity !== "all") out = out.filter((i) => i.severity === severity);
    if (namespace !== "all") out = out.filter((i) => i.namespace === namespace);
    if (status !== "all") {
      out = out.filter((i) => {
        if (status === "auto_resolved") return i.stale_resolved === true;
        // The other filters explicitly EXCLUDE stale_resolved so the
        // operator can choose between "real escalations" vs "auto-skipped".
        if (status === "escalated") return i.escalated && !i.stale_resolved;
        if (status === "healed") return i.healed === true;
        if (status === "failed") return i.healed === false && !i.escalated;
        return true;
      });
    }
    const sorted = [...out].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "ts") cmp = (a.ts ?? "").localeCompare(b.ts ?? "");
      else if (sortKey === "severity")
        cmp = SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity);
      else if (sortKey === "namespace") cmp = (a.namespace ?? "").localeCompare(b.namespace ?? "");
      return sortDir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [items, q, severity, namespace, status, sortKey, sortDir]);

  const page = filtered.slice(offset, offset + PAGE);
  const total = filtered.length;
  const pageMax = Math.max(0, Math.ceil(total / PAGE) - 1);
  const pageNum = Math.floor(offset / PAGE);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) update({ dir: sortDir === "asc" ? "desc" : "asc" });
    else update({ sort: k, dir: "desc" });
  };

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <ArrowUpDown className="ml-1 inline h-3 w-3 opacity-50" />;
    return sortDir === "asc" ? (
      <ChevronUp className="ml-1 inline h-3 w-3" />
    ) : (
      <ChevronDown className="ml-1 inline h-3 w-3" />
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-64">
          <label className="text-xs text-muted-foreground">{t("common.search")}</label>
          <Input
            placeholder={t("incidents.searchPlaceholder")}
            value={q}
            onChange={(e) => update({ q: e.target.value })}
          />
        </div>
        <FilterSelect
          label={t("incidents.filters.severity")}
          value={severity}
          options={[
            { value: "all", label: t("common.all") },
            ...SEVERITIES.map((s) => ({ value: s, label: s })),
          ]}
          onChange={(v) => update({ severity: v })}
        />
        <FilterSelect
          label={t("incidents.filters.namespace")}
          value={namespace}
          options={[
            { value: "all", label: t("common.all") },
            ...namespaces.map((n) => ({ value: n, label: n })),
          ]}
          onChange={(v) => update({ namespace: v })}
        />
        <FilterSelect
          label={t("incidents.filters.status")}
          value={status}
          options={[
            { value: "all", label: t("common.all") },
            { value: "healed", label: t("incidents.outcomes.healed") },
            { value: "escalated", label: t("incidents.outcomes.escalated") },
            { value: "failed", label: t("incidents.outcomes.failed") },
            {
              value: "auto_resolved",
              label: t("incidents.outcomes.autoResolved", { defaultValue: "Auto-resolved" }),
            },
          ]}
          onChange={(v) => update({ status: v })}
        />
      </div>

      {/* Bulk action bar — appears only when selection is non-empty. */}
      {selected.size > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/40 bg-primary/5 px-3 py-2">
          <span className="text-sm">
            {t("incidents.bulk.selectedCount", {
              count: selected.size,
              defaultValue: `${selected.size} selected`,
            })}
          </span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                if (
                  window.confirm(
                    t("incidents.bulk.confirmHealed", {
                      defaultValue:
                        "Delete selected healed incidents only? (escalated / failed ones will be kept.)",
                    }),
                  )
                ) {
                  bulkDelete.mutate({ ids: Array.from(selected), onlyHealed: true });
                }
              }}
              disabled={bulkDelete.isPending}
            >
              {bulkDelete.isPending ? <Spinner /> : <Trash2 className="h-3.5 w-3.5" />}
              {t("incidents.bulk.deleteHealed", { defaultValue: "Delete healed" })}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                if (
                  window.confirm(
                    t("incidents.bulk.confirmAll", {
                      count: selected.size,
                      defaultValue: `Permanently delete ${selected.size} incident(s)? This removes the JSON + trace; the audit log is unchanged.`,
                    }),
                  )
                ) {
                  bulkDelete.mutate({ ids: Array.from(selected), onlyHealed: false });
                }
              }}
              disabled={bulkDelete.isPending}
            >
              {bulkDelete.isPending ? <Spinner /> : <Trash2 className="h-3.5 w-3.5" />}
              {t("incidents.bulk.deleteAll", { defaultValue: "Delete selected" })}
            </Button>
          </div>
        </div>
      ) : null}

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  aria-label={t("incidents.bulk.selectAll", {
                    defaultValue: "Select all on this page",
                  })}
                  className="h-4 w-4 cursor-pointer"
                  checked={page.length > 0 && page.every((i) => selected.has(i.incident_id))}
                  onChange={(e) => {
                    setSelected((prev) => {
                      const next = new Set(prev);
                      if (e.target.checked) for (const i of page) next.add(i.incident_id);
                      else for (const i of page) next.delete(i.incident_id);
                      return next;
                    });
                  }}
                />
              </TableHead>
              <TableHead onClick={() => toggleSort("ts")} className="cursor-pointer select-none">
                {t("incidents.columns.time")}
                <SortIcon k="ts" />
              </TableHead>
              <TableHead
                onClick={() => toggleSort("severity")}
                className="cursor-pointer select-none"
              >
                {t("incidents.columns.severity")}
                <SortIcon k="severity" />
              </TableHead>
              <TableHead
                onClick={() => toggleSort("namespace")}
                className="cursor-pointer select-none"
              >
                {t("incidents.columns.namespace")}
                <SortIcon k="namespace" />
              </TableHead>
              <TableHead>{t("incidents.columns.pod")}</TableHead>
              <TableHead className="max-w-md">{t("incidents.columns.diagnosis")}</TableHead>
              <TableHead>{t("incidents.columns.action")}</TableHead>
              <TableHead>{t("incidents.columns.outcome")}</TableHead>
              <TableHead>{t("incidents.columns.mode")}</TableHead>
              <TableHead className="w-24 text-right">
                {t("incidents.columns.actions", { defaultValue: "Actions" })}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="py-10 text-center text-muted-foreground">
                  {t("incidents.empty")}
                </TableCell>
              </TableRow>
            ) : (
              page.map((i) => (
                <TableRow
                  key={i.incident_id}
                  className={selected.has(i.incident_id) ? "bg-accent/30" : ""}
                >
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label={t("incidents.bulk.selectRow", {
                        defaultValue: "Select incident",
                      })}
                      className="h-4 w-4 cursor-pointer"
                      checked={selected.has(i.incident_id)}
                      onChange={() => toggleOne(i.incident_id)}
                    />
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer whitespace-nowrap font-mono text-xs"
                  >
                    {i.ts ? format(new Date(i.ts), "Pp", { locale: dfLocale }) : "—"}
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer"
                  >
                    <SeverityChip severity={i.severity} />
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer font-mono text-xs"
                  >
                    {i.namespace ?? "—"}
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer font-mono text-xs truncate max-w-[12rem]"
                  >
                    {i.pod ?? "—"}
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer max-w-md truncate text-xs"
                  >
                    {i.diagnosis_summary ?? <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer"
                  >
                    {i.proposed_tool ? <Badge variant="secondary">{i.proposed_tool}</Badge> : "—"}
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer"
                  >
                    <OutcomeBadge incident={i} />
                  </TableCell>
                  <TableCell
                    onClick={() => navigate(`/incidents/${i.incident_id}`)}
                    className="cursor-pointer"
                  >
                    <ModeBadge mode={i.agent_mode} />
                  </TableCell>
                  <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("incidents.actions.replay", {
                          defaultValue: "Replay in pipeline",
                        })}
                        title={t("incidents.actions.replay", {
                          defaultValue: "Replay in pipeline",
                        })}
                        onClick={() => navigate(`/pipeline/${i.incident_id}`)}
                      >
                        <PlayCircle className="h-4 w-4" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("common.delete")}
                        title={t("common.delete")}
                        onClick={() => {
                          if (
                            window.confirm(
                              t("incidents.confirmDelete", {
                                id: i.incident_id,
                                defaultValue: `Delete incident ${i.incident_id}? (audit log is unchanged)`,
                              }),
                            )
                          ) {
                            singleDelete.mutate(i.incident_id);
                          }
                        }}
                        disabled={
                          singleDelete.isPending && singleDelete.variables === i.incident_id
                        }
                      >
                        {singleDelete.isPending && singleDelete.variables === i.incident_id ? (
                          <Spinner />
                        ) : (
                          <Trash2 className="h-4 w-4 text-danger" />
                        )}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {t("incidents.pagination.showing", {
            from: total === 0 ? 0 : offset + 1,
            to: Math.min(offset + PAGE, total),
            total,
          })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={pageNum <= 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            {t("incidents.pagination.prev")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={pageNum >= pageMax}
            onClick={() => setOffset(offset + PAGE)}
          >
            {t("incidents.pagination.next")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="text-xs text-muted-foreground">{label}</label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
