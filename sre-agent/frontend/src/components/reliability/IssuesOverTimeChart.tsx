import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Legend,
  CartesianGrid,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { IncidentSummary } from "@/lib/api";

/**
 * "Issues Over Time" — stacked bar chart matching Komodor's
 * reliability dashboard. One bar per day for the last 30 days;
 * stacked by outcome bucket so the operator can see at a glance
 * "did we have a bad day three weeks ago, and did the agent heal
 * it or page out?"
 *
 * Data source: GET /api/incidents?limit=… filtered to the last
 * `windowDays` days (default 30). The chart bins by local-day.
 */

interface Props {
  incidents: IncidentSummary[];
  windowDays?: number;
}

interface DayBin {
  date: string; // "MMM d" label
  healed: number;
  auto: number;
  escalated: number;
  failed: number;
}

export function IssuesOverTimeChart({ incidents, windowDays = 30 }: Props) {
  const { t } = useTranslation();
  const data = useMemo<DayBin[]>(() => {
    const bins = new Map<string, DayBin>();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    // Seed the bins for every day in the window so the X axis is
    // contiguous (no missing days where 0 incidents happened).
    for (let i = windowDays - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(today.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      bins.set(key, {
        date: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        healed: 0,
        auto: 0,
        escalated: 0,
        failed: 0,
      });
    }
    for (const inc of incidents) {
      const ts = new Date(inc.ts);
      if (!Number.isFinite(ts.getTime())) continue;
      ts.setHours(0, 0, 0, 0);
      const key = ts.toISOString().slice(0, 10);
      const bin = bins.get(key);
      if (!bin) continue;
      if (inc.stale_resolved) bin.auto += 1;
      else if (inc.healed === true) bin.healed += 1;
      else if (inc.healed === false) bin.failed += 1;
      else if (inc.escalated) bin.escalated += 1;
    }
    return Array.from(bins.values());
  }, [incidents, windowDays]);

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm uppercase tracking-wider">
          {t("reliability.chart.title", {
            defaultValue: `Issues over time · last ${windowDays} days`,
            count: windowDays,
          })}
        </CardTitle>
      </CardHeader>
      <CardContent className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.15} />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} interval={Math.floor(windowDays / 8)} />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar
              dataKey="healed"
              stackId="a"
              name={t("reliability.chart.healed", { defaultValue: "Healed" })}
              fill="hsl(var(--ok))"
            />
            <Bar
              dataKey="auto"
              stackId="a"
              name={t("reliability.chart.auto", { defaultValue: "Auto-resolved" })}
              fill="hsl(var(--info))"
            />
            <Bar
              dataKey="escalated"
              stackId="a"
              name={t("reliability.chart.escalated", { defaultValue: "Escalated" })}
              fill="hsl(var(--warn))"
            />
            <Bar
              dataKey="failed"
              stackId="a"
              name={t("reliability.chart.failed", { defaultValue: "Failed" })}
              fill="hsl(var(--danger))"
            />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
