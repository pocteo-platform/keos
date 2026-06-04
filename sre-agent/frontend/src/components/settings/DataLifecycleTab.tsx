import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, HardDrive, Loader2, RotateCcw, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

/**
 * Settings → Data Lifecycle tab — Phase C of the edge-install design.
 *
 * Three operator-tunable retention windows + a live disk-usage panel.
 * Live edits hit `PUT /api/retention` and persist into the overlay
 * store; the pruner picks them up on the next sweep (≤ 1h) without
 * a pod restart. Reset reverts to env defaults from the Helm chart.
 *
 * Visual layout matches the design doc's wireframe:
 *
 *   ┌──────────────────────────────────────────────────┐
 *   │  RETENTION SETTINGS                  [overlay]   │
 *   │  Audit log    [30] days  HMAC-chained record…    │
 *   │  Incidents    [90] days  Per-incident snapshots  │
 *   │  Traces       [30] days  Per-node start/end …    │
 *   │                            [Reset]   [Save]      │
 *   ├──────────────────────────────────────────────────┤
 *   │  DISK USAGE          Sweep every 1h              │
 *   │  Audit          142 MB  487 files                │
 *   │  Incidents       18 MB  1,234 files              │
 *   │  Traces          34 MB  1,234 files              │
 *   │  Calibration      2 MB                           │
 *   │  Baselines        5 MB                           │
 *   │  TOTAL          201 MB                           │
 *   └──────────────────────────────────────────────────┘
 */
export function DataLifecycleTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["retention"],
    queryFn: api.getRetention,
    refetchInterval: 30_000,
  });

  // Local form state so the operator can drag values around before
  // committing. Reset to API state whenever the API state changes.
  const [audit, setAudit] = useState<string>("");
  const [incidents, setIncidents] = useState<string>("");
  const [traces, setTraces] = useState<string>("");
  const apiAudit = q.data?.days.audit ?? null;
  const apiIncidents = q.data?.days.incidents ?? null;
  const apiTraces = q.data?.days.traces ?? null;

  useEffect(() => {
    if (apiAudit !== null) setAudit(String(apiAudit));
  }, [apiAudit]);
  useEffect(() => {
    if (apiIncidents !== null) setIncidents(String(apiIncidents));
  }, [apiIncidents]);
  useEffect(() => {
    if (apiTraces !== null) setTraces(String(apiTraces));
  }, [apiTraces]);

  const dirty =
    String(apiAudit ?? "") !== audit ||
    String(apiIncidents ?? "") !== incidents ||
    String(apiTraces ?? "") !== traces;

  const save = useMutation({
    mutationFn: () =>
      api.putRetention({
        days: {
          audit: Math.max(0, parseInt(audit) || 0),
          incidents: Math.max(0, parseInt(incidents) || 0),
          traces: Math.max(0, parseInt(traces) || 0),
        },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["retention"] }),
  });

  const reset = useMutation({
    mutationFn: () => api.putRetention({ reset: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["retention"] }),
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
              <ShieldCheck className="h-4 w-4 text-info" />
              {t("settings.lifecycle.title", { defaultValue: "Retention windows" })}
            </CardTitle>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("settings.lifecycle.subtitle", {
                defaultValue:
                  "How long the agent keeps audit records, incidents, and pipeline traces before the pruner deletes them. Set 0 to keep forever.",
              })}
            </p>
          </div>
          {q.data ? (
            <Badge
              variant={q.data.source === "overlay" ? "info" : "secondary"}
              className="font-mono text-[10px] uppercase"
            >
              {q.data.source === "overlay"
                ? t("settings.lifecycle.overlay", { defaultValue: "operator override" })
                : t("settings.lifecycle.envDefault", { defaultValue: "helm default" })}
            </Badge>
          ) : null}
        </CardHeader>

        <CardContent className="space-y-3">
          <RetentionRow
            label={t("settings.lifecycle.audit", { defaultValue: "Audit log" })}
            value={audit}
            onChange={setAudit}
            explain={t("settings.lifecycle.auditExplain", {
              defaultValue:
                "HMAC-chained record of every action the agent takes. Long retention is good forensically; short retention saves disk.",
            })}
          />
          <RetentionRow
            label={t("settings.lifecycle.incidents", { defaultValue: "Incidents" })}
            value={incidents}
            onChange={setIncidents}
            explain={t("settings.lifecycle.incidentsExplain", {
              defaultValue:
                "Per-incident state + outcome JSON. Powers the Incidents list + drill-down. After deletion, audit records still describe what happened.",
            })}
          />
          <RetentionRow
            label={t("settings.lifecycle.traces", { defaultValue: "Pipeline traces" })}
            value={traces}
            onChange={setTraces}
            explain={t("settings.lifecycle.tracesExplain", {
              defaultValue:
                "Per-node start/end records used by the Pipeline replay view. Largest bucket per incident; safe to keep short.",
            })}
          />

          <div className="flex items-center justify-end gap-2 pt-1">
            {q.data?.source === "overlay" ? (
              <Button
                variant="ghost"
                size="sm"
                disabled={reset.isPending || save.isPending}
                onClick={() => reset.mutate()}
                className="gap-1.5"
                title={t("settings.lifecycle.resetTooltip", {
                  defaultValue:
                    "Delete the overlay and revert to the values in the Helm chart.",
                })}
              >
                <RotateCcw className="h-3 w-3" />
                {t("settings.lifecycle.reset", { defaultValue: "Reset to defaults" })}
              </Button>
            ) : null}
            <Button
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
              className="gap-1.5"
            >
              {save.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              {t("common.save", { defaultValue: "Save" })}
            </Button>
          </div>

          <p className="border-l-2 border-info/40 bg-info/5 px-2 py-1.5 text-[11px] leading-snug text-foreground/80">
            {t("settings.lifecycle.howItWorks", {
              defaultValue:
                "Changes take effect on the next pruner sweep — no pod restart needed. The pruner runs every hour by default; UI edits and Helm chart defaults are both honoured, with UI edits winning on conflict.",
            })}
          </p>
        </CardContent>
      </Card>

      <DiskUsageCard
        usage={q.data?.disk_usage}
        sweepIntervalS={q.data?.sweep_interval_s ?? 3600}
      />
    </div>
  );
}

function RetentionRow({
  label,
  value,
  onChange,
  explain,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  explain: string;
}) {
  return (
    <div className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[12rem_8rem_1fr]">
      <div className="text-sm font-medium">{label}</div>
      <div className="relative">
        <Input
          type="number"
          min={0}
          max={3650}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 pr-12 text-right font-mono"
        />
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">
          days
        </span>
      </div>
      <p className="text-[11px] leading-snug text-muted-foreground">{explain}</p>
    </div>
  );
}

function DiskUsageCard({
  usage,
  sweepIntervalS,
}: {
  usage:
    | {
        audit_bytes: number;
        audit_files: number;
        incident_bytes: number;
        incident_files: number;
        trace_bytes: number;
        trace_files: number;
        calibration_bytes: number;
        baselines_bytes: number;
        total_bytes: number;
      }
    | undefined;
  sweepIntervalS: number;
}) {
  const { t } = useTranslation();
  if (!usage) {
    return null;
  }
  const rows = [
    {
      key: "audit",
      label: t("settings.lifecycle.disk.audit", { defaultValue: "Audit log" }),
      bytes: usage.audit_bytes,
      count: usage.audit_files,
    },
    {
      key: "incidents",
      label: t("settings.lifecycle.disk.incidents", { defaultValue: "Incidents" }),
      bytes: usage.incident_bytes,
      count: usage.incident_files,
    },
    {
      key: "traces",
      label: t("settings.lifecycle.disk.traces", { defaultValue: "Traces" }),
      bytes: usage.trace_bytes,
      count: usage.trace_files,
    },
    {
      key: "calibration",
      label: t("settings.lifecycle.disk.calibration", { defaultValue: "Calibration store" }),
      bytes: usage.calibration_bytes,
      count: 1,
    },
    {
      key: "baselines",
      label: t("settings.lifecycle.disk.baselines", { defaultValue: "Baselines store" }),
      bytes: usage.baselines_bytes,
      count: 1,
    },
  ];
  const maxBytes = Math.max(1, ...rows.map((r) => r.bytes));
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <HardDrive className="h-4 w-4 text-info" />
          {t("settings.lifecycle.disk.title", { defaultValue: "Disk usage" })}
        </CardTitle>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="secondary" className="cursor-help font-mono text-[10px]">
              {t("settings.lifecycle.sweepBadge", {
                defaultValue: "sweep every {{m}}min",
                m: Math.round(sweepIntervalS / 60),
              })}
            </Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs text-xs">
            {t("settings.lifecycle.sweepTooltip", {
              defaultValue:
                "How often the pruner walks each bucket and deletes files older than their retention window. Reads from the live overlay store, so UI edits apply on the next sweep.",
            })}
          </TooltipContent>
        </Tooltip>
      </CardHeader>
      <CardContent>
        <table className="w-full text-xs">
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-border/60 last:border-0">
                <td className="py-1.5 pr-3 text-foreground/80">
                  <div className="flex items-center gap-2">
                    <Database className="h-3 w-3 text-muted-foreground" />
                    <span>{r.label}</span>
                  </div>
                  {/* Inline bar — proportional to the largest bucket */}
                  <div className="mt-1 h-1 w-full bg-muted">
                    <div
                      className={cn(
                        "h-full bg-info/70",
                        r.bytes === 0 && "bg-muted-foreground/20",
                      )}
                      style={{ width: `${Math.round((r.bytes / maxBytes) * 100)}%` }}
                    />
                  </div>
                </td>
                <td className="py-1.5 pr-3 text-right font-mono">{fmtBytes(r.bytes)}</td>
                <td className="py-1.5 text-right font-mono text-muted-foreground">
                  {r.count > 0 ? `${r.count.toLocaleString()} ${r.count === 1 ? "file" : "files"}` : "—"}
                </td>
              </tr>
            ))}
            <tr className="border-t-2 border-border">
              <td className="py-1.5 pr-3 font-semibold uppercase tracking-wider text-[10px]">
                {t("settings.lifecycle.disk.total", { defaultValue: "Total" })}
              </td>
              <td className="py-1.5 pr-3 text-right font-mono font-semibold">
                {fmtBytes(usage.total_bytes)}
              </td>
              <td />
            </tr>
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function fmtBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}
