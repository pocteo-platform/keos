import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { History } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { IncidentSummary } from "@/lib/api";

/**
 * Most-recent terminal incidents with their outcome — the
 * dashboard's equivalent of Komodor's "events table" row, but
 * focused on what the AGENT did (healed/escalated/failed) rather
 * than raw cluster events.
 *
 * Also computes the 24h outcome ratio (healed / escalated /
 * failed) so the operator sees the trend at a glance.
 */
export function RecentOutcomesCard() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["incidents", { limit: 12 }],
    queryFn: () => api.listIncidents({ limit: 12 }),
    refetchInterval: 10_000,
  });
  const items = q.data?.items ?? [];

  const buckets = items.reduce(
    (acc, inc) => {
      if (inc.stale_resolved) acc.auto += 1;
      else if (inc.escalated) acc.escalated += 1;
      else if (inc.healed === true) acc.healed += 1;
      else if (inc.healed === false) acc.failed += 1;
      else acc.other += 1;
      return acc;
    },
    { healed: 0, escalated: 0, failed: 0, auto: 0, other: 0 },
  );
  const total = items.length;

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <History className="h-4 w-4 text-muted-foreground" />
          {t("dashboard.recent.title", { defaultValue: "Recent outcomes" })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Stacked horizontal bar: ratio of healed / escalated / failed
            over the last N incidents. Empty cluster shows a gentle
            dashed placeholder rather than a misleading 100%-green bar. */}
        <div className="mb-3 flex h-3 overflow-hidden border border-border">
          {total === 0 ? (
            <div className="flex-1 bg-muted" />
          ) : (
            <>
              <div className="bg-ok" style={{ width: `${(buckets.healed / total) * 100}%` }} />
              <div className="bg-info" style={{ width: `${(buckets.auto / total) * 100}%` }} />
              <div
                className="bg-warn"
                style={{ width: `${(buckets.escalated / total) * 100}%` }}
              />
              <div className="bg-danger" style={{ width: `${(buckets.failed / total) * 100}%` }} />
              <div
                className="bg-muted-foreground/40"
                style={{ width: `${(buckets.other / total) * 100}%` }}
              />
            </>
          )}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          <LegendDot color="bg-ok" label={`${buckets.healed} healed`} />
          <LegendDot color="bg-info" label={`${buckets.auto} auto`} />
          <LegendDot color="bg-warn" label={`${buckets.escalated} escalated`} />
          <LegendDot color="bg-danger" label={`${buckets.failed} failed`} />
        </div>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("dashboard.recent.empty", {
              defaultValue: "No incidents recorded yet.",
            })}
          </p>
        ) : (
          <ul className="space-y-1">
            {items.slice(0, 5).map((inc) => (
              <li key={inc.incident_id}>
                <Link
                  to={`/incidents/${inc.incident_id}`}
                  className="flex items-baseline gap-2 px-1 py-1 text-xs hover:bg-accent"
                >
                  <OutcomeDot inc={inc} />
                  <span className="truncate flex-1 font-mono">{inc.rule ?? "—"}</span>
                  <span className="text-muted-foreground">
                    {inc.namespace ?? "?"}/{inc.pod ?? "?"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn("h-2 w-2", color)} aria-hidden />
      {label}
    </span>
  );
}

function OutcomeDot({ inc }: { inc: IncidentSummary }) {
  let cls = "bg-muted-foreground/50";
  if (inc.stale_resolved) cls = "bg-info";
  else if (inc.escalated) cls = "bg-warn";
  else if (inc.healed === true) cls = "bg-ok";
  else if (inc.healed === false) cls = "bg-danger";
  return <span className={cn("h-2 w-2 shrink-0", cls)} aria-hidden />;
}
