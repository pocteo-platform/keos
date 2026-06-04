import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown, Activity, Brain, ChevronRight, Search } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { api, type ForecastLevel, type ForecastRow, type BaselineSnapshotModel } from "@/lib/api";

/**
 * Phase 14.7 — Settings runtime tabs gain LIVE panels alongside the
 * env-var knob list. Operators want to see what the forecaster /
 * baseline learner are actually doing — not just their config. The
 * env vars stay as a secondary reference (operators still edit them
 * in `.env.local` / `values.yaml`), but the primary surface is now
 * real `/api/forecasts` and `/api/baselines` data with refetch every
 * 8 s.
 */

interface KnobRow {
  /** i18n-key tail under `settings.knobs`. */
  key: string;
  defaultLabel: string;
  /** Env var name to render. */
  env: string;
  /** Sensible default value (matches agent/config.py / forecaster.py). */
  defaultValue: string;
  /** One-line operator-facing hint. */
  defaultHint: string;
}

const FORECASTING_KNOBS: KnobRow[] = [
  {
    key: "forecastTick",
    defaultLabel: "Scheduler tick",
    env: "FORECAST_TICK_INTERVAL_S",
    defaultValue: "60",
    defaultHint: "How often the forecaster walks every fitted baseline.",
  },
  {
    key: "forecastWindow",
    defaultLabel: "Fit window",
    env: "FORECAST_FIT_WINDOW",
    defaultValue: "60",
    defaultHint: "Samples per OLS fit. Larger = smoother, slower.",
  },
  {
    key: "forecastDedup",
    defaultLabel: "Dedup window",
    env: "FORECAST_DEDUP_WINDOW_S",
    defaultValue: "300",
    defaultHint: "Cooldown per (metric, level) before re-publishing.",
  },
];

const BASELINE_KNOBS: KnobRow[] = [
  {
    key: "prometheusUrl",
    defaultLabel: "Prometheus URL",
    env: "PROMETHEUS_URL",
    defaultValue: "k8s-proxy://monitoring/prometheus:9090",
    defaultHint:
      "Direct httpx URL or `k8s-proxy://` scheme. Empty disables baselines + forecasts.",
  },
  {
    key: "scrapeInterval",
    defaultLabel: "Scrape interval",
    env: "BASELINE_SCRAPE_INTERVAL_S",
    defaultValue: "60",
    defaultHint: "How often the learner polls Prometheus.",
  },
  {
    key: "queryProfile",
    defaultLabel: "Query profile",
    env: "BASELINE_QUERY_PROFILE",
    defaultValue: "k8s",
    defaultHint:
      "`k8s` for in-cluster Prometheus (pod-shaped labels); `docker` for compose-side cAdvisor.",
  },
];

const NOTIFICATION_KNOBS: KnobRow[] = [
  {
    key: "slack",
    defaultLabel: "Slack webhook",
    env: "SLACK_WEBHOOK_URL",
    defaultValue: "(unset)",
    defaultHint: "Where `page_human` posts when escalation fires.",
  },
  {
    key: "generic",
    defaultLabel: "Generic webhook",
    env: "PAGE_HUMAN_WEBHOOK_URL",
    defaultValue: "(unset)",
    defaultHint: "PagerDuty / Opsgenie / Discord / Teams / custom JSON sink.",
  },
  {
    key: "alertmanager",
    defaultLabel: "Alertmanager bearer",
    env: "ALERTMANAGER_WEBHOOK_TOKEN",
    defaultValue: "(unset)",
    defaultHint: "Inbound webhook auth for the /api/webhooks/alertmanager endpoint.",
  },
];

function KnobSection({ knobs, configHint }: { knobs: KnobRow[]; configHint: string }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wider">
          {t("settings.knobs.envvarsTitle", { defaultValue: "Configuration (env vars)" })}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{configHint}</p>
        <ul className="space-y-2">
          {knobs.map((k) => (
            <li key={k.env} className="border border-border bg-muted/20 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs font-semibold">
                  {t(`settings.knobs.${k.key}.label`, { defaultValue: k.defaultLabel })}
                </span>
                <Badge variant="secondary" className="font-mono text-[10px]">
                  {k.env}
                </Badge>
                <span
                  className={cn(
                    "ml-auto font-mono text-xs",
                    k.defaultValue === "(unset)" ? "text-muted-foreground" : "text-foreground",
                  )}
                >
                  {k.defaultValue}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t(`settings.knobs.${k.key}.hint`, { defaultValue: k.defaultHint })}
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Forecasting — live panel
// ---------------------------------------------------------------------------

const FORECAST_LEVEL_VARIANT: Record<ForecastLevel, "danger" | "warn" | "info" | "ok" | "secondary"> = {
  critical: "danger",
  warning: "warn",
  watch: "info",
  safe: "ok",
  none: "secondary",
};

function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (Math.abs(x) >= 1000) return x.toFixed(0);
  return x.toFixed(digits);
}

function fmtETA(seconds: number | null): string {
  if (seconds === null || Number.isNaN(seconds) || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function ForecastingTab() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState("");
  const fq = useQuery({
    queryKey: ["forecasts", "settings"],
    queryFn: () => api.listForecasts(),
    refetchInterval: 8_000,
  });

  const items = (fq.data?.items ?? []).filter((r) => {
    if (!filter.trim()) return true;
    const n = filter.toLowerCase();
    return (
      r.metric.toLowerCase().includes(n) ||
      r.namespace.toLowerCase().includes(n) ||
      r.scope.toLowerCase().includes(n)
    );
  });

  // Sort: levels first (critical → warning → watch → safe → none), then by recency proxy (slope)
  const LEVEL_RANK: Record<ForecastLevel, number> = {
    critical: 0,
    warning: 1,
    watch: 2,
    safe: 3,
    none: 4,
  };
  items.sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level]);

  const stats = {
    total: fq.data?.total ?? 0,
    critical: items.filter((r) => r.level === "critical").length,
    warning: items.filter((r) => r.level === "warning").length,
    watch: items.filter((r) => r.level === "watch").length,
    safe: items.filter((r) => r.level === "safe").length,
    cold: items.filter((r) => r.level === "none").length,
  };

  return (
    <div className="space-y-4">
      {/* Summary chips */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <StatChip
          label={t("settings.forecast.stats.critical", { defaultValue: "Critical" })}
          value={stats.critical}
          variant="danger"
        />
        <StatChip
          label={t("settings.forecast.stats.warning", { defaultValue: "Warning" })}
          value={stats.warning}
          variant="warn"
        />
        <StatChip
          label={t("settings.forecast.stats.watch", { defaultValue: "Watch" })}
          value={stats.watch}
          variant="info"
        />
        <StatChip
          label={t("settings.forecast.stats.safe", { defaultValue: "Safe" })}
          value={stats.safe}
          variant="ok"
        />
        <StatChip
          label={t("settings.forecast.stats.cold", { defaultValue: "Cold-start" })}
          value={stats.cold}
          variant="secondary"
        />
      </div>

      {/* Live forecasts table */}
      <Card>
        <CardHeader className="space-y-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm uppercase tracking-wider">
              {t("settings.forecast.title", { defaultValue: "Active forecasts" })}
            </CardTitle>
            <Badge variant="secondary" className="font-mono text-[10px]">
              {fq.dataUpdatedAt ? `${Math.round((Date.now() - fq.dataUpdatedAt) / 1000)}s ago` : "—"}
            </Badge>
          </div>
          <div className="relative max-w-sm">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("common.search")}
              className="h-8 pl-7 text-xs"
            />
          </div>
        </CardHeader>
        <CardContent>
          {/* Educational primer — what an operator sees in the table
              below and how the four level chips relate to action. */}
          <div className="mb-3 border border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-foreground/80">
            <span className="font-semibold uppercase tracking-wider text-foreground">
              {t("settings.forecast.primerTitle", { defaultValue: "How forecasts work" })}
            </span>
            <span className="ml-1.5 text-muted-foreground">
              {t("settings.forecast.primer", {
                defaultValue:
                  "Every 60s the scheduler fits OLS over the last 60 baseline samples per metric. Three horizons: T+5m / T+15m / T+60m. Levels — critical = breach at T+5m, warning = T+15m, watch = T+60m, safe = trend stable, cold-start = R² too low or < 50 samples. Each breach fires an `infra.events.forecasted_<kind>` event the agent acts on PROACTIVELY (one severity level lower than the reactive equivalent).",
              })}
            </span>
          </div>
          {fq.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {t("common.loading", { defaultValue: "Loading…" })}
            </p>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<TrendingUp className="h-4 w-4" />}
              text={t("settings.forecast.empty", {
                defaultValue:
                  "No forecasts yet. The scheduler needs ≥ 50 samples per metric before it can fit.",
              })}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <Th>
                      {t("settings.forecast.cols.level", { defaultValue: "Level" })}
                    </Th>
                    <Th>{t("settings.forecast.cols.metric", { defaultValue: "Metric" })}</Th>
                    <Th>{t("settings.forecast.cols.scope", { defaultValue: "Scope" })}</Th>
                    <Th align="right">
                      {t("settings.forecast.cols.current", { defaultValue: "Current" })}
                    </Th>
                    <Th align="right">
                      {t("settings.forecast.cols.sigma", { defaultValue: "σ above" })}
                    </Th>
                    <Th align="right">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="cursor-help underline decoration-dotted">
                            {t("settings.forecast.cols.t5", { defaultValue: "T+5m" })}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          {t("settings.forecast.cols.t5Hint", {
                            defaultValue: "ETA to threshold breach at +5min horizon.",
                          })}
                        </TooltipContent>
                      </Tooltip>
                    </Th>
                    <Th align="right">
                      {t("settings.forecast.cols.t15", { defaultValue: "T+15m" })}
                    </Th>
                    <Th align="right">
                      {t("settings.forecast.cols.r2", { defaultValue: "R²" })}
                    </Th>
                    <Th align="right">N</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((r) => (
                    <ForecastRowView key={`${r.metric}|${r.namespace}|${r.scope}`} row={r} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <KnobSection
        knobs={FORECASTING_KNOBS}
        configHint={t("settings.knobs.forecasting.config", {
          defaultValue:
            "Edit these in `.env.local` (laptop) or the Helm `values.yaml` (cluster) and restart the api container.",
        })}
      />
    </div>
  );
}

function ForecastRowView({ row }: { row: ForecastRow }) {
  const variant = FORECAST_LEVEL_VARIANT[row.level];
  return (
    <tr className="border-b border-border/60 hover:bg-accent/30">
      <td className="px-2 py-2">
        <Badge variant={variant} className="font-mono text-[10px] uppercase">
          {row.level}
        </Badge>
      </td>
      <td className="px-2 py-2 font-mono text-[11px]">{row.metric}</td>
      <td className="px-2 py-2 text-[11px] text-muted-foreground">
        {row.namespace} <ChevronRight className="inline h-3 w-3" /> {row.scope}
      </td>
      <td className="px-2 py-2 text-right font-mono">{fmtNum(row.current)}</td>
      <td className="px-2 py-2 text-right font-mono">
        {row.sigma_above !== null ? `${fmtNum(row.sigma_above, 1)}σ` : "—"}
      </td>
      <td className="px-2 py-2 text-right font-mono">{fmtETA(row.t5_min)}</td>
      <td className="px-2 py-2 text-right font-mono">{fmtETA(row.t15_min)}</td>
      <td className="px-2 py-2 text-right font-mono">
        {row.r2 !== null ? row.r2.toFixed(2) : "—"}
      </td>
      <td className="px-2 py-2 text-right font-mono text-muted-foreground">{row.n_samples}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Baselines — live panel
// ---------------------------------------------------------------------------

export function BaselinesTab() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState("");
  const bq = useQuery({
    queryKey: ["baselines", "settings"],
    queryFn: () => api.listBaselines(),
    refetchInterval: 8_000,
  });

  const items = (bq.data?.items ?? []).filter((r) => {
    if (!filter.trim()) return true;
    const n = filter.toLowerCase();
    return (
      r.metric.toLowerCase().includes(n) ||
      r.namespace.toLowerCase().includes(n) ||
      r.scope.toLowerCase().includes(n)
    );
  });

  const STATUS_RANK: Record<BaselineSnapshotModel["status"], number> = {
    ok: 0,
    learning: 1,
    missing: 2,
  };
  items.sort((a, b) => {
    const cmp = STATUS_RANK[a.status] - STATUS_RANK[b.status];
    if (cmp !== 0) return cmp;
    return (b.sigma_above ?? 0) - (a.sigma_above ?? 0);
  });

  const fitted = items.filter((r) => r.status === "ok").length;
  const learning = items.filter((r) => r.status === "learning").length;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <StatChip
          label={t("settings.baselines.stats.fitted", { defaultValue: "Fitted" })}
          value={fitted}
          variant="ok"
        />
        <StatChip
          label={t("settings.baselines.stats.learning", { defaultValue: "Learning" })}
          value={learning}
          variant="info"
        />
        <StatChip
          label={t("settings.baselines.stats.keys", { defaultValue: "Total keys" })}
          value={bq.data?.keys_known ?? 0}
          variant="secondary"
        />
        <StatChip
          label={t("settings.baselines.stats.samples", { defaultValue: "Samples" })}
          value={bq.data?.total_samples ?? 0}
          variant="secondary"
        />
      </div>

      <Card>
        <CardHeader className="space-y-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm uppercase tracking-wider">
              {t("settings.baselines.title", { defaultValue: "Per-key baselines" })}
            </CardTitle>
            <Badge variant="secondary" className="font-mono text-[10px]">
              {bq.dataUpdatedAt ? `${Math.round((Date.now() - bq.dataUpdatedAt) / 1000)}s ago` : "—"}
            </Badge>
          </div>
          <div className="relative max-w-sm">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("common.search")}
              className="h-8 pl-7 text-xs"
            />
          </div>
        </CardHeader>
        <CardContent>
          {/* Educational primer — what a baseline is, why σ matters,
              what 'fitted' vs 'learning' means. */}
          <div className="mb-3 border border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-foreground/80">
            <span className="font-semibold uppercase tracking-wider text-foreground">
              {t("settings.baselines.primerTitle", { defaultValue: "How baselines work" })}
            </span>
            <span className="ml-1.5 text-muted-foreground">
              {t("settings.baselines.primer", {
                defaultValue:
                  "The learner scrapes a fixed set of Prometheus metrics every 60s per (metric × namespace × pod) tuple. Each key gets an EWMA mean (recent-weighted average) + per-hour-of-day seasonal median. Status: ok = ≥ 50 samples, baseline is real; learning = < 50 samples, prompt notes 'still learning'. σ above = how far the CURRENT value is from the mean, in standard deviations — 1σ is mild, 3σ+ is anomalous. The diagnose prompt injects 'X is at Y, baseline Z ± W → N σ above' for every fitted key on the targeted pod.",
              })}
            </span>
          </div>
          {bq.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {t("common.loading", { defaultValue: "Loading…" })}
            </p>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Brain className="h-4 w-4" />}
              text={t("settings.baselines.empty", {
                defaultValue:
                  "No baselines yet. Set PROMETHEUS_URL and let the learner scrape for ≥ 5 minutes.",
              })}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <Th>{t("settings.baselines.cols.status", { defaultValue: "Status" })}</Th>
                    <Th>{t("settings.baselines.cols.metric", { defaultValue: "Metric" })}</Th>
                    <Th>{t("settings.baselines.cols.scope", { defaultValue: "Scope" })}</Th>
                    <Th align="right">
                      {t("settings.baselines.cols.current", { defaultValue: "Current" })}
                    </Th>
                    <Th align="right">
                      {t("settings.baselines.cols.mean", { defaultValue: "Mean" })}
                    </Th>
                    <Th align="right">
                      {t("settings.baselines.cols.std", { defaultValue: "Std" })}
                    </Th>
                    <Th align="right">
                      {t("settings.baselines.cols.sigma", { defaultValue: "σ above" })}
                    </Th>
                    <Th align="right">N</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((r) => (
                    <BaselineRowView
                      key={`${r.metric}|${r.namespace}|${r.scope}`}
                      row={r}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <KnobSection
        knobs={BASELINE_KNOBS}
        configHint={t("settings.knobs.baselines.config", {
          defaultValue:
            "Baselines require Prometheus. Apply manifests/monitoring.yaml for in-cluster install (Phase 13c).",
        })}
      />
    </div>
  );
}

function BaselineRowView({ row }: { row: BaselineSnapshotModel }) {
  const statusVariant: Record<BaselineSnapshotModel["status"], "ok" | "info" | "secondary"> = {
    ok: "ok",
    learning: "info",
    missing: "secondary",
  };
  const sigma = row.sigma_above;
  const sigmaTone =
    sigma === null
      ? "text-muted-foreground"
      : sigma >= 3
        ? "text-danger"
        : sigma >= 2
          ? "text-warn"
          : sigma >= 1
            ? "text-info"
            : "text-muted-foreground";
  return (
    <tr className="border-b border-border/60 hover:bg-accent/30">
      <td className="px-2 py-2">
        <Badge variant={statusVariant[row.status]} className="font-mono text-[10px] uppercase">
          {row.status}
        </Badge>
      </td>
      <td className="px-2 py-2 font-mono text-[11px]">{row.metric}</td>
      <td className="px-2 py-2 text-[11px] text-muted-foreground">
        {row.namespace} <ChevronRight className="inline h-3 w-3" /> {row.scope}
      </td>
      <td className="px-2 py-2 text-right font-mono">{fmtNum(row.current)}</td>
      <td className="px-2 py-2 text-right font-mono">{fmtNum(row.mean)}</td>
      <td className="px-2 py-2 text-right font-mono text-muted-foreground">{fmtNum(row.std)}</td>
      <td className={cn("px-2 py-2 text-right font-mono", sigmaTone)}>
        {sigma !== null ? `${fmtNum(sigma, 1)}σ` : "—"}
      </td>
      <td className="px-2 py-2 text-right font-mono text-muted-foreground">{row.n_samples}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Notifications tab — unchanged read-only
// ---------------------------------------------------------------------------

export function NotificationsTab() {
  const { t } = useTranslation();
  return (
    <KnobSection
      knobs={NOTIFICATION_KNOBS}
      configHint={t("settings.knobs.notifications.config", {
        defaultValue:
          "Either webhook URL is optional; both can be set at once. Both get the same JSON payload when `page_human` fires.",
      })}
    />
  );
}

// ---------------------------------------------------------------------------
// Drift tab (Phase 15) — surfaces recent drift events from /api/audit
// ---------------------------------------------------------------------------

export function DriftTab() {
  const { t } = useTranslation();
  // Drift events surface through the audit chain — they're emitted by
  // the detector and consumed by the graph. Recent NATS publishes also
  // land in audit as `chaos_observer.publish` records. We pull recent
  // audit, filter for drift-shape entries, render newest first.
  const auditQ = useQuery({
    queryKey: ["audit", "drift"],
    queryFn: () => api.listAudit({ limit: 500 }),
    refetchInterval: 8_000,
  });

  // Drift events arrive into the agent on subjects `infra.events.drift.<kind>`
  // and surface in the audit through the triage node. We filter by
  // event_type === "pipeline.triage.end" with payload.rule containing
  // "drift" — that's the canonical pre-LLM marker that says "this
  // incident was driven by drift".
  const items = (auditQ.data?.items ?? [])
    .filter((r) => {
      if (r.event_type === "drift.publish") return true;
      const p = r.payload as Record<string, unknown>;
      const rule = (p.rule as string | undefined) ?? "";
      return /drift/i.test(rule);
    })
    .slice()
    .reverse()
    .slice(0, 100);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("settings.drift.title", { defaultValue: "Recent drift events" })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {auditQ.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {t("common.loading", { defaultValue: "Loading…" })}
            </p>
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Activity className="h-4 w-4" />}
              text={t("settings.drift.empty", {
                defaultValue:
                  "No drift events yet. The detector publishes when live state diverges from /manifests.",
              })}
            />
          ) : (
            <ul className="space-y-1.5 text-xs">
              {items.map((r) => {
                const p = r.payload as Record<string, unknown>;
                const rule = (p.rule as string | undefined) ?? r.event_type;
                const fields = (p.output_fields as Record<string, unknown> | undefined) ?? {};
                const kind = (fields["k8s.kind"] as string | undefined) ?? "?";
                const name = (fields["k8s.name"] as string | undefined) ?? "?";
                const ns = (fields["k8s.namespace"] as string | undefined) ?? "?";
                const paths = (fields["drift.paths"] as string[] | undefined) ?? [];
                return (
                  <li
                    key={`${r.incident_id}-${r.ts}-${r.event_type}`}
                    className="flex items-start gap-2 border-l-2 border-warn/40 bg-warn/5 px-2 py-1.5"
                  >
                    <Badge variant="warn" className="font-mono text-[10px]">
                      drift
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-1">
                        <span className="font-mono font-semibold">
                          {kind}/{name}
                        </span>
                        <span className="text-[10px] text-muted-foreground">ns={ns}</span>
                        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                          {new Date(r.ts).toLocaleTimeString()}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                        {rule}
                      </div>
                      {paths.length > 0 ? (
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {paths.slice(0, 4).map((p, i) => (
                            <code
                              key={i}
                              className="border border-border bg-background px-1 text-[9px]"
                            >
                              {p}
                            </code>
                          ))}
                          {paths.length > 4 ? (
                            <span className="text-[9px] text-muted-foreground">
                              +{paths.length - 4}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("settings.drift.knobs", { defaultValue: "Detector configuration (env vars)" })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            <li className="border border-border bg-muted/20 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs font-semibold">
                  {t("settings.drift.sourceType", { defaultValue: "Source type" })}
                </span>
                <Badge variant="secondary" className="font-mono text-[10px]">
                  DRIFT_SOURCE_TYPE
                </Badge>
                <span className="ml-auto font-mono text-xs">manifests</span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t("settings.drift.sourceTypeHint", {
                  defaultValue: "`manifests` | `none`. Set to `none` to disable the detector.",
                })}
              </p>
            </li>
            <li className="border border-border bg-muted/20 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs font-semibold">
                  {t("settings.drift.sourcePath", { defaultValue: "Manifests path" })}
                </span>
                <Badge variant="secondary" className="font-mono text-[10px]">
                  DRIFT_SOURCE_PATH
                </Badge>
                <span className="ml-auto font-mono text-xs">/manifests</span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t("settings.drift.sourcePathHint", {
                  defaultValue:
                    "Directory the detector walks. Bind-mounted from `./manifests` in compose.",
                })}
              </p>
            </li>
            <li className="border border-border bg-muted/20 px-3 py-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-xs font-semibold">
                  {t("settings.drift.tick", { defaultValue: "Tick interval" })}
                </span>
                <Badge variant="secondary" className="font-mono text-[10px]">
                  DRIFT_TICK_INTERVAL_S
                </Badge>
                <span className="ml-auto font-mono text-xs">120</span>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {t("settings.drift.tickHint", {
                  defaultValue: "How often the detector polls the source-of-truth.",
                })}
              </p>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared little components
// ---------------------------------------------------------------------------

function StatChip({
  label,
  value,
  variant,
}: {
  label: string;
  value: number;
  variant: "danger" | "warn" | "info" | "ok" | "secondary";
}) {
  const rail: Record<string, string> = {
    danger: "border-l-danger bg-danger/5",
    warn: "border-l-warn bg-warn/5",
    info: "border-l-info bg-info/5",
    ok: "border-l-ok bg-ok/5",
    secondary: "border-l-border bg-muted/20",
  };
  const tone: Record<string, string> = {
    danger: "text-danger",
    warn: "text-warn",
    info: "text-info",
    ok: "text-ok",
    secondary: "text-muted-foreground",
  };
  return (
    <div className={cn("border border-border border-l-4 px-3 py-2", rail[variant])}>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("text-lg font-semibold tabular-nums", tone[variant])}>{value}</div>
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={cn(
        "px-2 py-2 font-medium uppercase tracking-wider",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

function EmptyState({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center">
      <div className="text-muted-foreground">{icon}</div>
      <p className="max-w-sm text-xs text-muted-foreground">{text}</p>
    </div>
  );
}

// Keep TrendingDown import live to avoid tree-shake removing it
// from the prepared icon set (we plan to use it once forecasts have
// a "trending DOWN" signal too).
void TrendingDown;
void Button;
