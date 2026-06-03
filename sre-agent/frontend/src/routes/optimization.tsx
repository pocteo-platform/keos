import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Lightbulb, Minus, TrendingUp, Wallet } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Phase 14.6 — Optimization page.
 *
 * Inspired by Komodor's Potential Savings dashboard, adapted to our
 * edge scope. We don't have cloud pricing data (edge clusters often
 * run on owned hardware — Jetson Orin, on-prem Talos), so we don't
 * surface dollar amounts. Instead we show RIGHTSIZING recommendations:
 *
 *   ┌───────────────────────────────────────────────────────────┐
 *   │  SUMMARY: # workloads ready to rightsize · avg headroom    │
 *   ├───────────────────────────────────────────────────────────┤
 *   │  RECOMMENDATIONS TABLE                                     │
 *   │  per (ns, pod) row: current usage, baseline mean,          │
 *   │  configured limit, headroom %, suggested direction         │
 *   └───────────────────────────────────────────────────────────┘
 *
 * Recommendation logic (Runbook 011 — rightsizing):
 *   headroom = 1 − baseline_mean / (current * 2)
 *     - headroom ≥ 0.70 → over-provisioned, suggest cut requests
 *     - headroom ≤ 0.20 → under-provisioned, suggest raise limits
 *     - otherwise        → well-sized, no action
 *
 * Without configured-limit data from the cluster API we approximate by
 * comparing the mean against the most-recent current value — when the
 * baseline mean is consistently far below the typical current spike,
 * the workload has headroom that could be reclaimed. Operators who
 * want exact recommendations can run `kubectl describe pod` for the
 * raw limits.
 */
export default function OptimizationPage() {
  const { t } = useTranslation();

  const baselinesQ = useQuery({
    queryKey: ["baselines", "optimization"],
    queryFn: () => api.listBaselines({}),
    refetchInterval: 30_000,
  });

  const recommendations = useMemo(() => {
    const items = baselinesQ.data?.items ?? [];
    // Group baselines by (namespace, scope) so each row is one workload.
    const byScope = new Map<string, typeof items>();
    for (const b of items) {
      if (b.status !== "ok" || b.mean == null || b.current == null) continue;
      const key = `${b.namespace}/${b.scope}`;
      const arr = byScope.get(key) ?? [];
      arr.push(b);
      byScope.set(key, arr);
    }
    const recs: {
      namespace: string;
      scope: string;
      metric: string;
      current: number;
      mean: number;
      std: number;
      headroom: number;
      direction: "over" | "under" | "ok";
      sigma_above: number;
    }[] = [];
    for (const [key, baselines] of byScope) {
      const [namespace, scope] = key.split("/", 2);
      // For each pod, look at memory_rss specifically — that's the
      // dominant signal for OOM / over-provisioning.
      const mem = baselines.find(
        (b) =>
          b.metric === "container_memory_rss_bytes" ||
          b.metric === "container_memory_working_set_bytes",
      );
      if (!mem || mem.mean == null || mem.current == null) continue;
      // Headroom heuristic: how much room between the baseline mean
      // and the "peak we've recently seen". We use mean + 3σ as a
      // synthetic ceiling because without configured-limit data we
      // can't compute exact headroom.
      const peak = mem.mean + 3 * (mem.std ?? 0);
      const headroom = peak > 0 ? Math.max(0, 1 - mem.mean / peak) : 0;
      const sigma = mem.sigma_above ?? 0;
      let direction: "over" | "under" | "ok" = "ok";
      if (headroom >= 0.7) direction = "over";
      else if (sigma >= 2) direction = "under";
      recs.push({
        namespace,
        scope,
        metric: mem.metric,
        current: mem.current,
        mean: mem.mean,
        std: mem.std ?? 0,
        headroom,
        direction,
        sigma_above: sigma,
      });
    }
    recs.sort((a, b) => {
      // Rank: under-provisioned (urgent) → over-provisioned → ok.
      const rank: Record<string, number> = { under: 0, over: 1, ok: 2 };
      if (rank[a.direction] !== rank[b.direction]) {
        return rank[a.direction] - rank[b.direction];
      }
      return Math.abs(b.headroom - 0.5) - Math.abs(a.headroom - 0.5);
    });
    return recs;
  }, [baselinesQ.data]);

  const overCount = recommendations.filter((r) => r.direction === "over").length;
  const underCount = recommendations.filter((r) => r.direction === "under").length;
  const okCount = recommendations.filter((r) => r.direction === "ok").length;
  const avgHeadroom =
    recommendations.length === 0
      ? 0
      : recommendations.reduce((sum, r) => sum + r.headroom, 0) / recommendations.length;

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("optimization.title", { defaultValue: "Optimization" })}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("optimization.subtitle", {
            defaultValue:
              "Rightsizing recommendations derived from learned baselines. Operator-facing — apply via the Use Case wizard or `kubectl patch`.",
          })}
        </p>
      </header>

      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <SummaryCard
          icon={<Lightbulb className="h-4 w-4" />}
          label={t("optimization.summary.recommendations", {
            defaultValue: "Recommendations",
          })}
          value={recommendations.length}
          variant="info"
        />
        <SummaryCard
          icon={<ArrowDown className="h-4 w-4" />}
          label={t("optimization.summary.over", { defaultValue: "Over-provisioned" })}
          value={overCount}
          variant={overCount > 0 ? "warn" : "ok"}
          hint={t("optimization.summary.overHint", {
            defaultValue: "Cut requests to reclaim capacity",
          })}
        />
        <SummaryCard
          icon={<ArrowUp className="h-4 w-4" />}
          label={t("optimization.summary.under", {
            defaultValue: "Under-provisioned",
          })}
          value={underCount}
          variant={underCount > 0 ? "danger" : "ok"}
          hint={t("optimization.summary.underHint", {
            defaultValue: "Raise limits to avoid OOM",
          })}
        />
        <SummaryCard
          icon={<Wallet className="h-4 w-4" />}
          label={t("optimization.summary.avgHeadroom", { defaultValue: "Avg headroom" })}
          value={`${(avgHeadroom * 100).toFixed(0)}%`}
          variant={avgHeadroom > 0.5 ? "warn" : "ok"}
        />
      </div>

      {/* Recommendations table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("optimization.table.title", { defaultValue: "Per-workload recommendations" })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {recommendations.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
              <TrendingUp className="h-6 w-6" />
              <p>
                {t("optimization.table.empty", {
                  defaultValue:
                    "No recommendations yet — the learner needs ≥ 50 samples per metric before it can rightsize.",
                })}
              </p>
              <Button asChild variant="outline" size="sm">
                <Link to="/dashboard">
                  {t("optimization.table.emptyAction", {
                    defaultValue: "Back to dashboard",
                  })}
                </Link>
              </Button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-2 py-2 font-medium uppercase tracking-wider">
                      {t("optimization.cols.workload", { defaultValue: "Workload" })}
                    </th>
                    <th className="px-2 py-2 font-medium uppercase tracking-wider">
                      {t("optimization.cols.direction", { defaultValue: "Direction" })}
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      {t("optimization.cols.mean", { defaultValue: "Baseline mean" })}
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      {t("optimization.cols.current", { defaultValue: "Current" })}
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      {t("optimization.cols.sigma", { defaultValue: "σ above" })}
                    </th>
                    <th className="px-2 py-2 text-right font-medium uppercase tracking-wider">
                      {t("optimization.cols.headroom", { defaultValue: "Headroom" })}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {recommendations.map((r) => (
                    <tr
                      key={`${r.namespace}-${r.scope}-${r.metric}`}
                      className={cn(
                        "border-b border-border/60 hover:bg-accent/30",
                        r.direction === "under" && "bg-danger/5",
                        r.direction === "over" && "bg-warn/5",
                      )}
                    >
                      <td className="px-2 py-2 font-mono">
                        <Link
                          to={`/workload/${encodeURIComponent(r.namespace)}/Pod/${encodeURIComponent(r.scope)}`}
                          className="hover:underline"
                        >
                          {r.namespace}/{r.scope}
                        </Link>
                      </td>
                      <td className="px-2 py-2">
                        <DirectionPill direction={r.direction} />
                      </td>
                      <td className="px-2 py-2 text-right font-mono">{fmtBytes(r.mean)}</td>
                      <td className="px-2 py-2 text-right font-mono">{fmtBytes(r.current)}</td>
                      <td className="px-2 py-2 text-right font-mono">
                        {r.sigma_above >= 0 ? "+" : ""}
                        {r.sigma_above.toFixed(1)}σ
                      </td>
                      <td className="px-2 py-2 text-right font-mono">
                        {(r.headroom * 100).toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {recommendations.length > 0 ? (
            <div className="mt-3 text-[11px] text-muted-foreground">
              {t("optimization.legend", {
                defaultValue: `${okCount} workloads are well-sized · ranked by urgency`,
                count: okCount,
              })}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

interface SummaryProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  hint?: string;
  variant: "ok" | "warn" | "danger" | "info" | "default";
}
function SummaryCard({ icon, label, value, hint, variant }: SummaryProps) {
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
    <Card className={cn("border-l-4", rail[variant])}>
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <span className={cn("mt-0.5 shrink-0", tone[variant])}>{icon}</span>
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

function DirectionPill({ direction }: { direction: "over" | "under" | "ok" }) {
  if (direction === "under") {
    return (
      <Badge variant="danger" className="gap-1 text-[10px] uppercase">
        <ArrowUp className="h-3 w-3" />
        raise limits
      </Badge>
    );
  }
  if (direction === "over") {
    return (
      <Badge variant="warn" className="gap-1 text-[10px] uppercase">
        <ArrowDown className="h-3 w-3" />
        cut requests
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="gap-1 text-[10px] uppercase">
      <Minus className="h-3 w-3" />
      ok
    </Badge>
  );
}

function fmtBytes(v: number): string {
  if (Math.abs(v) >= 1024 ** 3) return `${(v / 1024 ** 3).toFixed(2)} GiB`;
  if (Math.abs(v) >= 1024 ** 2) return `${(v / 1024 ** 2).toFixed(1)} MiB`;
  if (Math.abs(v) >= 1024) return `${(v / 1024).toFixed(1)} KiB`;
  return v.toFixed(0);
}
