import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import { format } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { IncidentSummary } from "@/lib/api";

/**
 * Komodor-style horizontal time ribbon. For each incident touching
 * this workload over the last 24h (or whatever window is passed),
 * we render a colored pill at its proportional position on the
 * ribbon. Hover/click jumps into /pipeline/<id>.
 *
 *   ──●──────●●─────────●───────●───
 *     │      ││         │       │
 *  healed escal heal   fail    open
 *
 * Empty window renders a flat muted bar with a "no incidents"
 * caption — the silence-is-information principle.
 */

interface Props {
  incidents: IncidentSummary[];
  /** Window in milliseconds. Default 24h. */
  windowMs?: number;
}

export function EventsTimelineRibbon({ incidents, windowMs = 24 * 60 * 60 * 1000 }: Props) {
  const { t } = useTranslation();
  const now = Date.now();
  const windowStart = now - windowMs;

  const positioned = useMemo(() => {
    const pills: { inc: IncidentSummary; pct: number; variant: string }[] = [];
    for (const inc of incidents) {
      const ts = new Date(inc.ts).getTime();
      if (!Number.isFinite(ts) || ts < windowStart) continue;
      const pct = ((ts - windowStart) / windowMs) * 100;
      let variant = "bg-muted-foreground/40";
      if (inc.stale_resolved) variant = "bg-info";
      else if (inc.healed === true) variant = "bg-ok";
      else if (inc.healed === false) variant = "bg-danger";
      else if (inc.escalated) variant = "bg-warn";
      pills.push({ inc, pct, variant });
    }
    return pills;
  }, [incidents, windowMs, windowStart]);

  // Build the date markers (one per quarter of the window)
  const markers = useMemo(() => {
    const segments = 4;
    const out: { label: string; pct: number }[] = [];
    for (let i = 0; i <= segments; i++) {
      const ts = windowStart + (windowMs * i) / segments;
      out.push({
        label: format(new Date(ts), i === 0 || i === segments ? "MMM d HH:mm" : "HH:mm"),
        pct: (i / segments) * 100,
      });
    }
    return out;
  }, [windowMs, windowStart]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm uppercase tracking-wider">
          {t("workload.ribbon.title", {
            defaultValue: `Events · last ${Math.round(windowMs / (60 * 60 * 1000))}h`,
          })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="relative">
          {/* The ribbon track */}
          <div className="relative h-12 bg-muted/40">
            {positioned.length === 0 ? (
              <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
                {t("workload.ribbon.empty", {
                  defaultValue: "No incidents in this window — the workload has been healthy.",
                })}
              </div>
            ) : (
              positioned.map(({ inc, pct, variant }, i) => (
                <motion.div
                  key={inc.incident_id}
                  initial={{ opacity: 0, scale: 0.6 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.2, delay: i * 0.03 }}
                  style={{ left: `${Math.max(0, Math.min(100, pct))}%` }}
                  className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
                >
                  <Link
                    to={`/incidents/${inc.incident_id}`}
                    title={`${inc.rule ?? "incident"} · ${inc.severity ?? "?"} · ${format(
                      new Date(inc.ts),
                      "MMM d HH:mm:ss",
                    )}`}
                    className={cn(
                      "block h-6 w-6 cursor-pointer rounded-full border-2 border-background ring-2 ring-transparent transition-all hover:scale-125 hover:ring-foreground/30",
                      variant,
                    )}
                  />
                </motion.div>
              ))
            )}
          </div>
          {/* Date markers strip */}
          <div className="relative mt-2 h-4 text-[10px] text-muted-foreground">
            {markers.map((m, i) => (
              <span
                key={i}
                style={{ left: `${m.pct}%` }}
                className={cn(
                  "absolute -translate-x-1/2 font-mono whitespace-nowrap",
                  i === 0 && "translate-x-0",
                  i === markers.length - 1 && "-translate-x-full",
                )}
              >
                {m.label}
              </span>
            ))}
          </div>
        </div>
        {/* Legend */}
        <div className="mt-4 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          <LegendPill color="bg-ok" label={t("workload.ribbon.healed", { defaultValue: "healed" })} />
          <LegendPill
            color="bg-warn"
            label={t("workload.ribbon.escalated", { defaultValue: "escalated" })}
          />
          <LegendPill
            color="bg-danger"
            label={t("workload.ribbon.failed", { defaultValue: "failed" })}
          />
          <LegendPill color="bg-info" label={t("workload.ribbon.auto", { defaultValue: "auto" })} />
        </div>
      </CardContent>
    </Card>
  );
}

function LegendPill({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("h-2.5 w-2.5 rounded-full", color)} aria-hidden />
      {label}
    </span>
  );
}
