import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { useChannel } from "@/lib/ws";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  ExternalLink,
  RotateCw,
  Search,
  Sparkles,
  Wrench,
  XCircle,
  ShieldAlert,
  TrendingUp,
  Eye,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api, type AuditRecord } from "@/lib/api";

/**
 * Phase 14.7 — Dashboard "Live agent activity" ribbon.
 *
 * Streams the most recent audit events through a glanceable timeline.
 * Operators eyeballing the dashboard can see at a glance what the
 * agent is actually doing this minute — last RAG hit, last LLM
 * verdict, last action, last verify outcome. The component polls
 * /api/audit every 5 s (no WebSocket here — refetch on focus
 * suffices for a dashboard tile) and renders the newest 12 events
 * with a Framer-Motion entrance so freshly-arrived rows slide in.
 *
 * This is the "wow effect" tile — the rest of the dashboard tells
 * the operator WHAT is happening; this one tells them WHEN.
 */

/** Map an audit event_type → user-facing label + icon + tone. */
function decorate(
  r: AuditRecord,
): { icon: React.ReactNode; tone: string; label: string; detail: string | null } {
  const ev = r.event_type;
  const p = r.payload as Record<string, unknown>;
  const tool = p.tool as string | undefined;
  const summary = p.summary as string | undefined;
  const reason = p.reason as string | undefined;
  const allow = "allow" in p ? (p.allow as boolean) : null;
  const conf = p.confidence as number | undefined;
  const healed = "healed" in p ? (p.healed as boolean) : null;

  if (ev.startsWith("pipeline.triage")) {
    return {
      icon: <Eye className="h-3.5 w-3.5" />,
      tone: "text-info",
      label: "triage",
      detail: (p.rule as string | undefined) ?? null,
    };
  }
  if (ev.startsWith("pipeline.retrieve_context")) {
    return {
      icon: <Search className="h-3.5 w-3.5" />,
      tone: "text-muted-foreground",
      label: "retrieve",
      detail: p.rag_hits ? `${(p.rag_hits as unknown[]).length} hits` : null,
    };
  }
  if (ev.startsWith("pipeline.k8sgpt_analyze")) {
    return {
      icon: <Sparkles className="h-3.5 w-3.5" />,
      tone: "text-info",
      label: "diagnostic signals",
      detail: p.findings ? `${(p.findings as unknown[]).length} findings` : null,
    };
  }
  if (ev.startsWith("pipeline.enrich_with_baselines")) {
    return {
      icon: <Brain className="h-3.5 w-3.5" />,
      tone: "text-info",
      label: "baselines",
      detail: p.baselines ? `${(p.baselines as unknown[]).length} keys` : null,
    };
  }
  if (ev.startsWith("pipeline.diagnose")) {
    return {
      icon: <Brain className="h-3.5 w-3.5" />,
      tone: conf !== undefined && conf >= 0.9 ? "text-ok" : "text-info",
      label: "diagnose",
      detail: conf !== undefined ? `conf=${conf.toFixed(2)}` : summary ?? null,
    };
  }
  if (ev.startsWith("pipeline.plan_action")) {
    return {
      icon: <Wrench className="h-3.5 w-3.5" />,
      tone: "text-info",
      label: "plan",
      detail: tool ?? null,
    };
  }
  if (ev.startsWith("pipeline.policy_gate")) {
    return {
      icon: <ShieldAlert className="h-3.5 w-3.5" />,
      tone: allow === false ? "text-danger" : "text-info",
      label: "policy",
      detail: allow === false ? "deny" : allow === true ? "allow" : null,
    };
  }
  if (ev.startsWith("pipeline.execute")) {
    const dry = p.dry_run as boolean | undefined;
    return {
      icon: <Wrench className="h-3.5 w-3.5" />,
      tone: dry ? "text-muted-foreground" : "text-info",
      label: dry ? "execute (dry-run)" : "execute",
      detail: tool ?? null,
    };
  }
  if (ev.startsWith("pipeline.verify")) {
    return {
      icon:
        healed === true ? (
          <CheckCircle2 className="h-3.5 w-3.5" />
        ) : healed === false ? (
          <XCircle className="h-3.5 w-3.5" />
        ) : (
          <RotateCw className="h-3.5 w-3.5" />
        ),
      tone: healed === true ? "text-ok" : healed === false ? "text-warn" : "text-info",
      label: "verify",
      detail: healed === true ? "healed" : healed === false ? "not healed" : null,
    };
  }
  if (ev.startsWith("pipeline.escalate")) {
    return {
      icon: <AlertTriangle className="h-3.5 w-3.5" />,
      tone: "text-warn",
      label: "escalate",
      detail: reason ?? null,
    };
  }
  if (ev === "drift.publish" || /drift/i.test((p.rule as string) ?? "")) {
    return {
      icon: <TrendingUp className="h-3.5 w-3.5" />,
      tone: "text-warn",
      label: "drift",
      detail: (p.rule as string | undefined) ?? null,
    };
  }
  if (ev === "forecast.publish" || /forecasted/i.test((p.rule as string) ?? "")) {
    return {
      icon: <TrendingUp className="h-3.5 w-3.5" />,
      tone: "text-info",
      label: "forecast",
      detail: (p.rule as string | undefined) ?? null,
    };
  }
  return {
    icon: <Activity className="h-3.5 w-3.5" />,
    tone: "text-muted-foreground",
    label: ev,
    detail: summary ?? null,
  };
}

function relativeTime(iso: string): string {
  const dt = Date.now() - new Date(iso).getTime();
  if (dt < 1000) return "now";
  if (dt < 60_000) return `${Math.floor(dt / 1000)}s`;
  if (dt < 3_600_000) return `${Math.floor(dt / 60_000)}m`;
  return `${Math.floor(dt / 3_600_000)}h`;
}

export function LiveActivityRibbon() {
  const { t } = useTranslation();
  // Phase 14.7: was a 5s poll; Phase 14.11 upgrade: WS-subscribed.
  // Initial seed comes from the REST endpoint so the ribbon is
  // pre-populated on first render; thereafter `useChannel("audit")`
  // streams every new audit line. The fallback poll stays at a much
  // slower 30s so a transient WS drop still gets refreshed.
  const seed = useQuery({
    queryKey: ["audit", "live-ribbon-seed"],
    queryFn: () => api.listAudit({ limit: 12 }),
    refetchInterval: 30_000,
  });

  // Live tail buffer — newest first, capped at 12.
  const [tail, setTail] = useState<AuditRecord[]>([]);
  const seededRef = useRef(false);

  // Seed the tail from the REST snapshot on first arrival.
  useEffect(() => {
    if (seededRef.current) return;
    if (!seed.data?.items) return;
    seededRef.current = true;
    setTail(seed.data.items.slice().reverse().slice(0, 12));
  }, [seed.data]);

  // WS push: prepend new records, dedupe by (ts + incident_id + event_type
  // + hmac8) so a brief WS+REST overlap doesn't double-render.
  useChannel("audit", (event) => {
    const r = event as unknown as AuditRecord;
    if (!r || !r.event_type || !r.incident_id || !r.ts) return;
    setTail((prev) => {
      const key = `${r.ts}-${r.incident_id}-${r.event_type}-${(r.hmac ?? "").slice(0, 8)}`;
      const seen = new Set(
        prev.map((p) => `${p.ts}-${p.incident_id}-${p.event_type}-${(p.hmac ?? "").slice(0, 8)}`),
      );
      if (seen.has(key)) return prev;
      return [r, ...prev].slice(0, 12);
    });
  });

  // Background poll for ribbon: when WS is down, the 30s seed
  // refetch above ALSO re-seeds the tail with whatever's currently
  // in the audit log. We add a small effect to merge updates without
  // wiping locally-streamed entries.
  useEffect(() => {
    if (!seed.data?.items) return;
    const incoming = seed.data.items.slice().reverse().slice(0, 12);
    setTail((prev) => {
      // If the WS has been silent, prev might be a stale snapshot;
      // merge by key, prefer prev for already-seen records.
      const byKey = new Map<string, AuditRecord>();
      for (const r of [...incoming, ...prev]) {
        const k = `${r.ts}-${r.incident_id}-${r.event_type}-${(r.hmac ?? "").slice(0, 8)}`;
        if (!byKey.has(k)) byKey.set(k, r);
      }
      // Sort newest first.
      return [...byKey.values()]
        .sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0))
        .slice(0, 12);
    });
  }, [seed.data]);

  const items = useMemo(() => tail, [tail]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <Activity className="h-4 w-4 text-info" />
          {t("dashboard.activity.title", { defaultValue: "Live agent activity" })}
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant="info" className="font-mono text-[10px]">
            <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-info" />
            LIVE
          </Badge>
          <Link to="/settings?tab=audit" className="text-muted-foreground hover:text-foreground">
            <ExternalLink className="h-3.5 w-3.5" />
          </Link>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {seed.isLoading && items.length === 0 ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">
            {t("common.loading", { defaultValue: "Loading…" })}
          </p>
        ) : items.length === 0 ? (
          <p className="px-4 py-6 text-xs text-muted-foreground">
            {t("dashboard.activity.empty", {
              defaultValue: "Quiet on the cluster. Run a use case to see the agent in action.",
            })}
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            <AnimatePresence initial={false}>
              {items.map((r) => {
                const d = decorate(r);
                return (
                  <motion.li
                    key={`${r.incident_id}-${r.ts}-${r.event_type}-${r.hmac.slice(0, 8)}`}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="flex items-center gap-3 px-3 py-1.5 text-xs hover:bg-accent/30"
                  >
                    <span className={cn("shrink-0", d.tone)}>{d.icon}</span>
                    <span className="w-20 shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      {d.label}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-foreground/90">
                      {d.detail ?? <span className="text-muted-foreground">—</span>}
                    </span>
                    <Link
                      to={`/incidents/${r.incident_id}`}
                      className="hidden font-mono text-[10px] text-muted-foreground hover:text-foreground md:inline"
                      title={r.incident_id}
                    >
                      {r.incident_id.slice(0, 8)}
                    </Link>
                    <span className="w-10 shrink-0 text-right font-mono text-[10px] text-muted-foreground">
                      {relativeTime(r.ts)}
                    </span>
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
