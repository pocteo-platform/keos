import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Activity, AlertTriangle, Cpu, Gauge, Server, ShieldCheck } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

/**
 * Top-of-dashboard stat hero. Six chips in a horizontal row showing
 * the cluster's "are we OK?" at-a-glance. Komodor's workload-health
 * page does the same thing with chips like {Cluster | Type |
 * Namespace | Image | …}. We adapt to per-edge-cluster scope:
 *
 *   ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
 *   │ Mode │ │ Pods │ │ Inc. │ │ Conf │ │ Base │ │ Fcst │
 *   │active│ │ 8/8  │ │ 0 ⚠  │ │ 0.84 │ │ 67   │ │ 2 ↑  │
 *   └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘
 *
 * Each chip animates in on mount with stagger so the page feels
 * alive on first paint. Data is fetched in parallel via TanStack
 * Query — slow endpoints don't block the others from rendering.
 */

interface StatChipProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  /** Optional sub-line. Renders dimmer + smaller. */
  hint?: string;
  variant?: "ok" | "warn" | "danger" | "info" | "default";
  index?: number;
}

function StatChip({ icon, label, value, hint, variant = "default", index = 0 }: StatChipProps) {
  // Sharp-edged tinted background per Pocteo brand. Border-l-4 gives
  // the strong vertical color rail Komodor uses for status chips.
  const tint: Record<NonNullable<StatChipProps["variant"]>, string> = {
    ok: "border-l-ok bg-ok/5",
    warn: "border-l-warn bg-warn/5",
    danger: "border-l-danger bg-danger/5",
    info: "border-l-info bg-info/5",
    default: "border-l-border bg-card",
  };
  const iconColor: Record<NonNullable<StatChipProps["variant"]>, string> = {
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
    info: "text-info",
    default: "text-muted-foreground",
  };
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.04 }}
    >
      <Card className={cn("h-full border-l-4", tint[variant])}>
        <CardContent className="p-3">
          <div className="flex items-start gap-2">
            <span className={cn("mt-0.5 shrink-0", iconColor[variant])}>{icon}</span>
            <div className="min-w-0 flex-1">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {label}
              </div>
              <div className="truncate text-lg font-semibold leading-tight">{value}</div>
              {hint ? (
                <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</div>
              ) : null}
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

export function HeroStats() {
  const { t } = useTranslation();

  const modeQ = useQuery({ queryKey: ["mode"], queryFn: api.getMode, refetchInterval: 10_000 });
  const podsQ = useQuery({
    queryKey: ["pods", "all"],
    queryFn: () => api.listPods(),
    refetchInterval: 6_000,
  });
  const activeIncQ = useQuery({
    queryKey: ["active-incidents"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 4_000,
  });
  const calibQ = useQuery({
    queryKey: ["calibration"],
    queryFn: api.listCalibration,
    refetchInterval: 30_000,
  });
  const baselinesQ = useQuery({
    queryKey: ["baselines", "all"],
    queryFn: () => api.listBaselines({}),
    refetchInterval: 30_000,
  });
  const forecastsQ = useQuery({
    queryKey: ["forecasts", "all"],
    queryFn: () => api.listForecasts({}),
    refetchInterval: 15_000,
  });

  const pods = podsQ.data ?? [];
  const ready = pods.filter((p) => p.phase === "Running").length;
  const total = pods.length;
  const active = activeIncQ.data ?? [];
  const median = calibQ.data?.median_confidence;
  const samples = baselinesQ.data?.total_samples ?? 0;
  const keys = baselinesQ.data?.keys_known ?? 0;
  const anomalous = (forecastsQ.data?.items ?? []).filter(
    (f) => f.level === "warning" || f.level === "critical",
  ).length;
  const mode = modeQ.data?.mode ?? "—";

  const modeVariant: StatChipProps["variant"] =
    mode === "active" ? "ok" : mode === "observe-only" ? "info" : "warn";
  const podsVariant: StatChipProps["variant"] =
    total === 0 ? "default" : ready < total ? "warn" : "ok";
  const incVariant: StatChipProps["variant"] = active.length > 0 ? "warn" : "ok";
  const confVariant: StatChipProps["variant"] =
    median == null ? "default" : median >= 0.9 ? "ok" : median >= 0.7 ? "info" : "warn";
  const forecastVariant: StatChipProps["variant"] = anomalous > 0 ? "warn" : "ok";

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatChip
        index={0}
        icon={<ShieldCheck className="h-4 w-4" />}
        label={t("dashboard.hero.mode", { defaultValue: "Agent mode" })}
        value={mode}
        variant={modeVariant}
        hint={
          mode === "observe-only"
            ? t("dashboard.hero.modeObserveHint", { defaultValue: "Dry-run; no mutations." })
            : mode === "active"
              ? t("dashboard.hero.modeActiveHint", { defaultValue: "Autonomous remediation on." })
              : t("dashboard.hero.modeFrozenHint", { defaultValue: "Kill-switch engaged." })
        }
      />
      <StatChip
        index={1}
        icon={<Server className="h-4 w-4" />}
        label={t("dashboard.hero.pods", { defaultValue: "Pods ready" })}
        value={`${ready}/${total}`}
        variant={podsVariant}
        hint={t("dashboard.hero.podsHint", { defaultValue: "All workloads" })}
      />
      <StatChip
        index={2}
        icon={<AlertTriangle className="h-4 w-4" />}
        label={t("dashboard.hero.active", { defaultValue: "Active incidents" })}
        value={active.length}
        variant={incVariant}
        hint={
          active.length === 0
            ? t("dashboard.hero.activeQuiet", { defaultValue: "Quiet" })
            : t("dashboard.hero.activeBusy", { defaultValue: "Graph runs in progress" })
        }
      />
      <StatChip
        index={3}
        icon={<Gauge className="h-4 w-4" />}
        label={t("dashboard.hero.confidence", { defaultValue: "Median confidence" })}
        value={median == null ? "—" : median.toFixed(2)}
        variant={confVariant}
        hint={t("dashboard.hero.confidenceHint", { defaultValue: "Target ≥ 0.90" })}
      />
      <StatChip
        index={4}
        icon={<Cpu className="h-4 w-4" />}
        label={t("dashboard.hero.baselines", { defaultValue: "Baselines" })}
        value={`${keys} keys`}
        variant="info"
        hint={t("dashboard.hero.baselinesHint", {
          defaultValue: `${samples.toLocaleString()} samples`,
          count: samples,
        })}
      />
      <StatChip
        index={5}
        icon={<Activity className="h-4 w-4" />}
        label={t("dashboard.hero.forecasts", { defaultValue: "Forecast alerts" })}
        value={anomalous}
        variant={forecastVariant}
        hint={t("dashboard.hero.forecastsHint", {
          defaultValue: "Warning + critical",
        })}
      />
    </div>
  );
}
