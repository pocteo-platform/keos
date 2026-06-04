import { useTranslation } from "react-i18next";
import { HeroStats } from "@/components/dashboard/HeroStats";
import { ActiveIncidentsCard } from "@/components/dashboard/ActiveIncidentsCard";
import { RecentOutcomesCard } from "@/components/dashboard/RecentOutcomesCard";
import { ForecastWatchlist } from "@/components/dashboard/ForecastWatchlist";
import { NamespaceProblemMap } from "@/components/dashboard/NamespaceProblemMap";
import { LiveActivityRibbon } from "@/components/dashboard/LiveActivityRibbon";
import { ClusterContextCard } from "@/components/dashboard/ClusterContextCard";
import { useApp } from "@/lib/store";

/**
 * Phase 14.5b — Dashboard landing page. Replaces the old
 * `/ → /cluster` redirect. Operator's first view of the edge:
 *
 *   ┌───────────────────────────────────────────────────────────┐
 *   │  HEAD: title + agent loop tagline                          │
 *   ├───────────────────────────────────────────────────────────┤
 *   │  HERO STATS — 6 chips (mode, pods, incidents, conf,        │
 *   │              baselines, forecasts)                         │
 *   ├──────────────────┬──────────────────┬─────────────────────┤
 *   │  ActiveIncidents │  RecentOutcomes  │  ForecastWatchlist   │
 *   ├──────────────────┴──────────────────┴─────────────────────┤
 *   │  NAMESPACE PROBLEM MAP (table)                             │
 *   └───────────────────────────────────────────────────────────┘
 *
 * All sections poll independently; nothing on this page blocks
 * anything else. Live updates flow through TanStack Query's
 * refetchInterval; we don't subscribe to WS here (the pipeline
 * page does for graph events, and the polling cadences are fine
 * for dashboard-density data).
 */
export default function DashboardPage() {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("dashboard.title", { defaultValue: "Edge cluster overview" })}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("dashboard.subtitle", {
            defaultValue:
              "Observe → Predict → Heal → Learn — what the agent is seeing right now, locale " + locale,
          })}
        </p>
      </header>

      <HeroStats />

      <LiveActivityRibbon />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ActiveIncidentsCard />
        <RecentOutcomesCard />
        <ForecastWatchlist />
      </div>

      <NamespaceProblemMap />

      <ClusterContextCard />
    </div>
  );
}
