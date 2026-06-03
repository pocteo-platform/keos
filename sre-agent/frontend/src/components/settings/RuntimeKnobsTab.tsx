import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Settings → Forecasting + Baselines + Notifications. These knobs are
 * all driven by env vars at deploy time (not by /api endpoints), so the
 * UI presents them READ-ONLY with a hint that an operator must edit
 * `.env.local` or the Helm `values.yaml` to change them. This matches
 * how production deployments handle config — no surprise live edits
 * through the SPA.
 *
 * The doc/runbook links in each section point the operator to the
 * canonical place to make the change.
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

interface SectionProps {
  title: string;
  knobs: KnobRow[];
  configHint: string;
}

function Section({ title, knobs, configHint }: SectionProps) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wider">{title}</CardTitle>
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

export function ForecastingTab() {
  const { t } = useTranslation();
  return (
    <Section
      title={t("settings.knobs.forecasting.title", { defaultValue: "Forecaster" })}
      knobs={FORECASTING_KNOBS}
      configHint={t("settings.knobs.forecasting.config", {
        defaultValue:
          "Edit these in `.env.local` (laptop) or the Helm `values.yaml` (cluster) and restart the api container.",
      })}
    />
  );
}

export function BaselinesTab() {
  const { t } = useTranslation();
  return (
    <Section
      title={t("settings.knobs.baselines.title", { defaultValue: "Baseline learner" })}
      knobs={BASELINE_KNOBS}
      configHint={t("settings.knobs.baselines.config", {
        defaultValue:
          "Baselines require Prometheus. Apply manifests/monitoring.yaml for in-cluster install (Phase 13c).",
      })}
    />
  );
}

export function NotificationsTab() {
  const { t } = useTranslation();
  return (
    <Section
      title={t("settings.knobs.notifications.title", { defaultValue: "Notifications" })}
      knobs={NOTIFICATION_KNOBS}
      configHint={t("settings.knobs.notifications.config", {
        defaultValue:
          "Either webhook URL is optional; both can be set at once. Both get the same JSON payload when `page_human` fires.",
      })}
    />
  );
}
