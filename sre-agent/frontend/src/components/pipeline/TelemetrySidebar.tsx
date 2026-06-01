import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { phaseOfNode } from "@/hooks/usePhaseGrouping";
import { PHASE_VARIANT, type LoopPhase } from "@/lib/branding";
import type { PipelineState } from "@/hooks/usePipelineState";

type BadgeVariant = "warn" | "info" | "ok" | "default";
const phaseBadgeVariant = (phase: LoopPhase): BadgeVariant => {
  const v = PHASE_VARIANT[phase];
  return v === "primary" ? "default" : (v as BadgeVariant);
};

interface Props {
  /** Pipeline reducer state. Used to compute the "actions taken" stat and
   *  the action log on the right side. */
  state: PipelineState;
  /** Namespace whose pods to chart. Defaults to "workloads". */
  namespace?: string;
}

interface ChartPoint {
  t: number; // unix ms
  ready: number;
  restarts: number;
}

/** Right-side panel matching the reference design:
 *    - Container memory chart (proxied here as pods-ready over time)
 *    - Three stat boxes: Pods Ready, Total Restarts, Actions Taken
 *    - Agent action log — one line per pipeline node phase end, color-coded
 *
 *  Polls /api/cluster/pods every 4 s and keeps a rolling 60-point window. */
export function TelemetrySidebar({ state, namespace = "workloads" }: Props) {
  const { t } = useTranslation();
  const [series, setSeries] = useState<ChartPoint[]>([]);

  const podsQ = useQuery({
    queryKey: ["pods", namespace],
    queryFn: () => api.listPods(namespace),
    refetchInterval: 4000,
  });

  useEffect(() => {
    if (!podsQ.data) return;
    const ready = podsQ.data.filter((p) => p.phase === "Running").length;
    const restarts = podsQ.data.reduce((acc, p) => acc + p.restarts, 0);
    setSeries((prev) => [...prev, { t: Date.now(), ready, restarts }].slice(-60));
  }, [podsQ.data]);

  const totalRestarts = series.at(-1)?.restarts ?? 0;
  const podsReady = series.at(-1)?.ready ?? 0;
  const actionsTaken = useMemo(
    () =>
      Object.values(state.nodes).filter(
        (n) =>
          n.status === "done" && n.output && (n.output as Record<string, unknown>).action_result,
      ).length,
    [state.nodes],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Trend chart */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center justify-between text-xs uppercase tracking-wider text-muted-foreground">
            <span>{t("pipeline.timeline.telemetry.podsReadyOverTime")}</span>
            <span className="font-mono normal-case text-foreground">{podsReady}</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                <defs>
                  <linearGradient id="ready-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(217 91% 60%)" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="hsl(217 91% 60%)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" hide />
                <YAxis allowDecimals={false} width={30} tick={{ fontSize: 10 }} />
                <Tooltip
                  contentStyle={{ fontSize: 10, padding: "4px 8px" }}
                  labelFormatter={(v) => new Date(v as number).toLocaleTimeString()}
                />
                <Area
                  type="monotone"
                  dataKey="ready"
                  stroke="hsl(217 91% 60%)"
                  strokeWidth={2}
                  fill="url(#ready-fill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      {/* Stat boxes */}
      <div className="grid grid-cols-3 gap-2">
        <StatBox label={t("pipeline.timeline.telemetry.podsReady")} value={podsReady} />
        <StatBox label={t("pipeline.timeline.telemetry.restarts")} value={totalRestarts} />
        <StatBox label={t("pipeline.timeline.telemetry.actionsTaken")} value={actionsTaken} />
      </div>

      {/* Action log */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-xs uppercase tracking-wider text-muted-foreground">
            {t("pipeline.timeline.telemetry.actionLog")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 font-mono text-[10px] leading-relaxed">
          {buildActionLog(state).map((line, i) => (
            <div key={i} className="flex items-baseline gap-2">
              <Badge variant={phaseBadgeVariant(line.phase)} className="px-1 py-0 text-[9px]">
                {line.phase}
              </Badge>
              <span className="text-muted-foreground">{line.message}</span>
            </div>
          ))}
          {state.t0 === null ? (
            <p className="text-muted-foreground italic">
              {t("pipeline.timeline.telemetry.waiting")}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function StatBox({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border bg-card p-3">
      <div className="text-[9px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-bold">{value}</div>
    </div>
  );
}

interface LogLine {
  phase: LoopPhase;
  message: string;
}

function buildActionLog(state: PipelineState): LogLine[] {
  const out: LogLine[] = [];
  for (const [kind, node] of Object.entries(state.nodes)) {
    if (node.status === "pending" || node.status === "skipped") continue;
    const phase = phaseOfNode(kind as Parameters<typeof phaseOfNode>[0]);
    const summary = summariseNode(kind, node.output);
    if (summary) out.push({ phase, message: `[${kind}] ${summary}` });
  }
  return out;
}

function summariseNode(kind: string, output?: Record<string, unknown>): string | null {
  if (!output) return null;
  if (kind === "diagnose") {
    const d = (output.diagnosis as { confidence?: number; root_cause?: string }) ?? {};
    if (d.root_cause) return `${d.root_cause} (conf ${d.confidence ?? "?"})`;
  }
  if (kind === "plan_action") {
    const p = (output.proposed_action as { tool?: string; args?: Record<string, unknown> }) ?? {};
    if (p.tool) return `${p.tool} ${JSON.stringify(p.args ?? {})}`;
  }
  if (kind === "policy_gate") {
    const d = (output.policy_decision as { allow?: boolean; reasons?: string[] }) ?? {};
    return d.allow === true ? "ALLOW" : `DENY: ${d.reasons?.[0] ?? ""}`;
  }
  if (kind === "execute") {
    const r = (output.action_result as { success?: boolean; tool?: string }) ?? {};
    return `${r.tool ?? "?"} ${r.success ? "ok" : "FAIL"}`;
  }
  if (kind === "verify") {
    const v = (output.verification as { healed?: boolean; notes?: string }) ?? {};
    return `healed=${v.healed} ${v.notes ?? ""}`;
  }
  return null;
}
