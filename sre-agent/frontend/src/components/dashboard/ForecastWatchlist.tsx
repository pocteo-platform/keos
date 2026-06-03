import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { ForecastLevel } from "@/lib/api";

/**
 * Top forecasted breaches — the agent's predictive surface.
 * One row per (metric, namespace, pod) currently classified as
 * warning or critical. Each carries the σ-above-baseline,
 * the t+5min projection, and a small visual indicator of slope
 * direction (↑ for up, ↓ for down).
 *
 * Empty state explicitly tells the operator "no surge predicted"
 * rather than "nothing here" — silence on a forecasting page is
 * an information-bearing signal.
 */
export function ForecastWatchlist() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["forecasts", "all"],
    queryFn: () => api.listForecasts({}),
    refetchInterval: 15_000,
  });
  const items = q.data?.items ?? [];
  const anomalous = items.filter((f) => f.level === "warning" || f.level === "critical").slice(0, 8);

  const levelVariant: Record<ForecastLevel, "danger" | "warn" | "info" | "secondary"> = {
    critical: "danger",
    warning: "warn",
    watch: "info",
    safe: "secondary",
    none: "secondary",
  };

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <TrendingUp className="h-4 w-4 text-warn" />
          {t("dashboard.forecast.title", { defaultValue: "Forecast watchlist" })}
          {anomalous.length ? (
            <Badge variant="warn" className="ml-auto">
              {anomalous.length}
            </Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {anomalous.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {q.isLoading
              ? t("dashboard.forecast.loading", { defaultValue: "Loading forecasts…" })
              : t("dashboard.forecast.calm", {
                  defaultValue: "No surge predicted across any tracked metric.",
                })}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {anomalous.map((f) => {
              const slopeArrow =
                (f.slope_per_s ?? 0) > 0 ? "↑" : (f.slope_per_s ?? 0) < 0 ? "↓" : "→";
              return (
                <li
                  key={`${f.metric}-${f.namespace}-${f.scope}`}
                  className={cn(
                    "flex items-center gap-2 border-l-2 px-2 py-1.5 text-xs",
                    f.level === "critical" ? "border-danger" : "border-warn",
                  )}
                >
                  <Badge
                    variant={levelVariant[f.level]}
                    className="shrink-0 text-[10px] uppercase"
                  >
                    {f.level}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-1.5 truncate">
                      <span className="truncate font-mono">{f.metric}</span>
                      <span className="text-muted-foreground">
                        {f.namespace ? `${f.namespace}/` : ""}
                        {f.scope}
                      </span>
                    </div>
                    {f.sigma_above != null ? (
                      <p className="text-[10px] text-muted-foreground">
                        {f.sigma_above.toFixed(1)}σ above, r²={(f.r2 ?? 0).toFixed(2)}
                      </p>
                    ) : null}
                  </div>
                  <span
                    className={cn(
                      "shrink-0 font-mono text-base",
                      f.level === "critical" ? "text-danger" : "text-warn",
                    )}
                    aria-hidden
                  >
                    {slopeArrow}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
