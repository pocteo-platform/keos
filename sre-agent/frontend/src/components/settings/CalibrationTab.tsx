/**
 * Settings → Calibration tab. The body of the old top-level
 * /calibration route lives here unchanged — same per-rule
 * confidence table + override controls. We extract just the
 * table component so the wrapping route can render the title
 * outside the tab.
 */

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ShieldCheck,
  TrendingUp,
  AlertTriangle,
  ArrowUpDown,
  ChevronUp,
  ChevronDown,
  RotateCcw,
  Search,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type CalibrationRule } from "@/lib/api";

type SortKey = "rule" | "n" | "accuracy" | "p50" | "p90" | "threshold";
type SortDir = "asc" | "desc";

const NINETY_PCT_TARGET = 0.9;

function pct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}
function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toFixed(digits);
}
/** "operator override" / "learned (p25)" / "global default" — the
 *  three sources our gating threshold can come from. Returns the
 *  i18n KEY (resolved by the caller's `t()`) so the helper has no
 *  React/i18next coupling. */
function thresholdSourceKey(rule: CalibrationRule): string {
  if (rule.threshold_override !== null) return "settings.calibration.source.override";
  if (rule.n_graded >= 20) return "settings.calibration.source.learned";
  return "settings.calibration.source.global";
}
const SOURCE_DEFAULTS: Record<string, string> = {
  "settings.calibration.source.override": "operator override",
  "settings.calibration.source.learned": "learned (p25)",
  "settings.calibration.source.global": "global default",
};

export function CalibrationTab() {
  const { t } = useTranslation();
  const cal = useQuery({
    queryKey: ["calibration"],
    queryFn: api.listCalibration,
    refetchInterval: 10_000,
  });
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("n");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir("desc");
    }
  }

  const sortedRules = useMemo(() => {
    const arr = (cal.data?.items ?? []).filter((r) => {
      if (!filter.trim()) return true;
      return r.rule_id.toLowerCase().includes(filter.toLowerCase());
    });
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "rule":
          cmp = a.rule_id.localeCompare(b.rule_id);
          break;
        case "n":
          cmp = a.n_graded - b.n_graded;
          break;
        case "accuracy":
          cmp = (a.accuracy ?? -1) - (b.accuracy ?? -1);
          break;
        case "p50":
          cmp = (a.p50_confidence ?? -1) - (b.p50_confidence ?? -1);
          break;
        case "p90":
          cmp = (a.p90_confidence ?? -1) - (b.p90_confidence ?? -1);
          break;
        case "threshold":
          cmp = (a.effective_threshold ?? -1) - (b.effective_threshold ?? -1);
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [cal.data, filter, sortKey, sortDir]);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SummaryStat
          icon={<TrendingUp className="h-4 w-4" />}
          label={t("settings.calibration.summary.median", { defaultValue: "Median confidence" })}
          value={num(cal.data?.median_confidence ?? null, 2)}
          variant={
            (cal.data?.median_confidence ?? 0) >= NINETY_PCT_TARGET
              ? "ok"
              : (cal.data?.median_confidence ?? 0) >= 0.7
                ? "info"
                : "warn"
          }
          hint={t("settings.calibration.summary.medianHint", {
            defaultValue: "Target ≥ 0.90",
          })}
        />
        <SummaryStat
          icon={<ShieldCheck className="h-4 w-4" />}
          label={t("settings.calibration.summary.above90", { defaultValue: "Rules above 90%" })}
          value={pct(cal.data?.pct_above_90 ?? null, 0)}
          variant={(cal.data?.pct_above_90 ?? 0) >= 0.5 ? "ok" : "info"}
        />
        <SummaryStat
          icon={<AlertTriangle className="h-4 w-4" />}
          label={t("settings.calibration.summary.rules", { defaultValue: "Tracked rules" })}
          value={String(cal.data?.total ?? 0)}
          variant="default"
        />
      </div>

      <Card>
        <CardHeader className="space-y-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm uppercase tracking-wider">
              {t("settings.calibration.table.title", { defaultValue: "Per-rule calibration" })}
            </CardTitle>
            <Badge variant="secondary" className="font-mono text-[10px]">
              {sortedRules.length} / {cal.data?.items?.length ?? 0}
            </Badge>
          </div>
          {(cal.data?.items?.length ?? 0) > 0 ? (
            <div className="relative max-w-sm">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t("common.search")}
                className="h-8 pl-7 text-xs"
              />
            </div>
          ) : null}
        </CardHeader>
        <CardContent>
          {/* Educational primer — first-time operators need to understand
              what "calibration" means, why we track per-rule thresholds,
              and what an override actually does. */}
          <div className="mb-3 border border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-foreground/80">
            <span className="font-semibold uppercase tracking-wider text-foreground">
              {t("settings.calibration.primerTitle", {
                defaultValue: "How calibration works",
              })}
            </span>
            <span className="ml-1.5 text-muted-foreground">
              {t("settings.calibration.primer", {
                defaultValue:
                  "After each incident's `verify` step the grader updates per-rule counters: n_diagnosed, n_correct, p50/p90 confidence. Once a rule has ≥ 20 graded incidents, its effective threshold flips from the global default to `p25 of correct-incident confidence` — noisy rules end up with HIGH thresholds (escalate sooner), well-calibrated rules get LOW thresholds (act on lower confidence). The Pin button lets you force a specific threshold for any rule (operator override beats the learner). Reset (↺) clears your pin and lets the learner take over again.",
              })}
            </span>
          </div>
          {cal.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {t("common.loading", { defaultValue: "Loading…" })}
            </p>
          ) : (cal.data?.items?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("settings.calibration.table.empty", {
                defaultValue: "No rules graded yet — run a use case to populate.",
              })}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-2 py-2 font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "rule"}
                        dir={sortDir}
                        onClick={() => toggleSort("rule")}
                      >
                        {t("settings.calibration.cols.rule", { defaultValue: "Rule" })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "n"}
                        dir={sortDir}
                        onClick={() => toggleSort("n")}
                      >
                        {t("settings.calibration.cols.n", { defaultValue: "N" })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "accuracy"}
                        dir={sortDir}
                        onClick={() => toggleSort("accuracy")}
                      >
                        {t("settings.calibration.cols.accuracy", { defaultValue: "Accuracy" })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "p50"}
                        dir={sortDir}
                        onClick={() => toggleSort("p50")}
                      >
                        {t("settings.calibration.cols.p50", { defaultValue: "p50" })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "p90"}
                        dir={sortDir}
                        onClick={() => toggleSort("p90")}
                      >
                        {t("settings.calibration.cols.p90", { defaultValue: "p90" })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      <SortHeaderInner
                        active={sortKey === "threshold"}
                        dir={sortDir}
                        onClick={() => toggleSort("threshold")}
                      >
                        {t("settings.calibration.cols.threshold", {
                          defaultValue: "Effective threshold",
                        })}
                      </SortHeaderInner>
                    </th>
                    <th className="px-2 py-2 font-medium uppercase tracking-wider">
                      {t("settings.calibration.cols.source", { defaultValue: "Source" })}
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      {t("settings.calibration.cols.override", { defaultValue: "Override" })}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedRules.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="py-6 text-center text-muted-foreground">
                        {t("common.empty")}
                      </td>
                    </tr>
                  ) : (
                    sortedRules.map((r) => <RuleRow key={r.rule_id} rule={r} />)
                  )}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SortHeaderInner({
  active,
  dir,
  onClick,
  children,
}: {
  active: boolean;
  dir: SortDir;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 hover:text-foreground"
    >
      {children}
      {active ? (
        dir === "asc" ? (
          <ChevronUp className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        )
      ) : (
        <ArrowUpDown className="h-3 w-3 opacity-50" />
      )}
    </button>
  );
}

function RuleRow({ rule }: { rule: CalibrationRule }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [pending, setPending] = useState<string>(
    rule.threshold_override != null ? String(rule.threshold_override) : "",
  );
  const setMut = useMutation({
    mutationFn: (value: number | null) =>
      api.setCalibrationThreshold(rule.rule_id, value, "operator override via SPA"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["calibration"] }),
  });
  const acc = rule.accuracy;
  const accVariant = acc == null ? "secondary" : acc >= 0.9 ? "ok" : acc >= 0.7 ? "info" : "warn";
  const srcKey = thresholdSourceKey(rule);
  return (
    <tr className="border-b border-border/60 hover:bg-accent/30">
      <td className="px-2 py-2 font-mono">{rule.rule_id}</td>
      <td className="px-2 py-2 text-right font-mono">{rule.n_graded}</td>
      <td className="px-2 py-2 text-right">
        <Badge variant={accVariant} className="font-mono">
          {pct(acc, 0)}
        </Badge>
      </td>
      <td className="px-2 py-2 text-right font-mono">{num(rule.p50_confidence, 2)}</td>
      <td className="px-2 py-2 text-right font-mono">{num(rule.p90_confidence, 2)}</td>
      <td className="px-2 py-2 text-right font-mono">{num(rule.effective_threshold, 2)}</td>
      <td className="px-2 py-2 text-[10px] text-muted-foreground">
        {t(srcKey, { defaultValue: SOURCE_DEFAULTS[srcKey] })}
      </td>
      <td className="px-2 py-2">
        <div className="flex items-center justify-end gap-1">
          <Input
            value={pending}
            onChange={(e) => setPending(e.target.value)}
            placeholder="—"
            className="h-7 w-20 text-right font-mono text-xs"
          />
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-[10px]"
            onClick={() => {
              const v = pending.trim();
              setMut.mutate(v === "" ? null : parseFloat(v));
            }}
            disabled={setMut.isPending}
          >
            {t("settings.calibration.pin", { defaultValue: "Pin" })}
          </Button>
          {/* Reset button — only visible when an operator override
              is in effect. Clears it (back to learned/global) and
              empties the input so the source label flips next render. */}
          {rule.threshold_override !== null ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-1.5 text-muted-foreground hover:text-danger"
              title={t("settings.calibration.reset", {
                defaultValue: "Reset to learned threshold",
              })}
              onClick={() => {
                setPending("");
                setMut.mutate(null);
              }}
              disabled={setMut.isPending}
              aria-label={t("settings.calibration.reset", {
                defaultValue: "Reset to learned threshold",
              })}
            >
              <RotateCcw className="h-3 w-3" />
            </Button>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

function SummaryStat({
  icon,
  label,
  value,
  hint,
  variant,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  variant: "ok" | "warn" | "danger" | "info" | "default";
}) {
  const rail: Record<string, string> = {
    ok: "border-l-ok bg-ok/5",
    warn: "border-l-warn bg-warn/5",
    danger: "border-l-danger bg-danger/5",
    info: "border-l-info bg-info/5",
    default: "border-l-border",
  };
  const tone: Record<string, string> = {
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
    info: "text-info",
    default: "text-muted-foreground",
  };
  return (
    <Card className={`border-l-4 ${rail[variant]}`}>
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <span className={`mt-0.5 shrink-0 ${tone[variant]}`}>{icon}</span>
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {label}
            </div>
            <div className="text-xl font-semibold leading-tight">{value}</div>
            {hint ? <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div> : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
