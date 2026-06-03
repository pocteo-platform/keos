import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WorkloadHeaderCard } from "@/components/workload/WorkloadHeaderCard";
import { EventsTimelineRibbon } from "@/components/workload/EventsTimelineRibbon";
import { IncidentTable } from "@/components/incidents/IncidentTable";
import { OutcomeBadge } from "@/components/incidents/OutcomeBadge";
import { SeverityChip } from "@/components/incidents/SeverityChip";
import { api } from "@/lib/api";
import { cn, relativeTimeSeconds } from "@/lib/utils";

/**
 * Phase 14.5c — workload drill-down.
 *
 * Route: `/workload/:ns/:kind/:name` (kind ∈ Pod | Deployment)
 *
 *   ┌────────────────────────────────────────────────────────┐
 *   │  ← back to cluster                                      │
 *   ├────────────────────────────────────────────────────────┤
 *   │  WORKLOAD HEADER CARD (chips + history breakdown)       │
 *   ├────────────────────────────────────────────────────────┤
 *   │  EVENTS TIMELINE RIBBON (24h, colored pills)            │
 *   ├────────────────────────────────────────────────────────┤
 *   │  [Incidents] [Baselines] [Forecasts]                    │
 *   │  …tabbed body…                                          │
 *   └────────────────────────────────────────────────────────┘
 *
 * All data is composed from existing endpoints:
 *   - /api/cluster/pods?ns=         → PodSummary
 *   - /api/cluster/deployments?ns=  → DeploymentSummary
 *   - /api/incidents?ns=&pod=       → IncidentList (Phase 14.5a filter)
 *   - /api/incidents/active         → ActiveIncident[]
 *   - /api/baselines?ns=&pod=       → BaselineList
 *   - /api/forecasts?ns=&pod=       → ForecastList
 *
 * No new backend endpoint needed — the workload page is a pure
 * frontend composition over what already exists.
 */
export default function WorkloadPage() {
  const { t } = useTranslation();
  const params = useParams<{ ns: string; kind: string; name: string }>();
  const ns = params.ns ?? "";
  const kindRaw = (params.kind ?? "Pod").toLowerCase();
  const kind: "Pod" | "Deployment" = kindRaw === "deployment" ? "Deployment" : "Pod";
  const name = params.name ?? "";

  // --- Pod or Deployment summary -----------------------------------
  const podsQ = useQuery({
    queryKey: ["pods", ns],
    queryFn: () => api.listPods(ns),
    refetchInterval: 6_000,
    enabled: !!ns,
  });
  const deploysQ = useQuery({
    queryKey: ["deployments", ns],
    queryFn: () => api.listDeployments(ns),
    refetchInterval: 10_000,
    enabled: !!ns,
  });
  const pod = useMemo(
    () => (kind === "Pod" ? (podsQ.data ?? []).find((p) => p.name === name) ?? null : null),
    [podsQ.data, kind, name],
  );
  const deployment = useMemo(
    () =>
      kind === "Deployment"
        ? (deploysQ.data ?? []).find((d) => d.name === name) ?? null
        : null,
    [deploysQ.data, kind, name],
  );

  // --- Incident history for THIS workload --------------------------
  // For Deployment kind, fall back to filtering by ns only — the
  // backend filter is `pod=`, and we don't have a deployment-name
  // filter today. Frontend post-filters to incidents whose pod owner
  // matches the deployment name. Good enough for the Phase 14.5c
  // demo; the next pass should add a `deployment=` filter to the
  // /api/incidents endpoint.
  const incidentsQ = useQuery({
    queryKey: ["incidents", { ns, pod: kind === "Pod" ? name : undefined, limit: 50 }],
    queryFn: () =>
      api.listIncidents({
        namespace: ns,
        pod: kind === "Pod" ? name : undefined,
        limit: 50,
      }),
    refetchInterval: 8_000,
  });
  const incidents = useMemo(() => {
    const all = incidentsQ.data?.items ?? [];
    if (kind === "Pod") return all;
    // Deployment: filter to incidents whose pod looks like
    // <deployment>-<replicaset-hash>-<pod-hash>.
    return all.filter(
      (i) => i.pod?.startsWith(`${name}-`) || i.pod === name,
    );
  }, [incidentsQ.data, kind, name]);

  // --- Active incidents (for the header chip) ---------------------
  const activeQ = useQuery({
    queryKey: ["active-incidents"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 3_000,
  });
  const activeCount = useMemo(() => {
    const all = activeQ.data ?? [];
    if (kind === "Pod") {
      return all.filter((a) => a.namespace === ns && a.pod === name).length;
    }
    return all.filter((a) => a.namespace === ns && a.pod?.startsWith(`${name}-`)).length;
  }, [activeQ.data, kind, ns, name]);

  // --- Baselines + forecasts for THIS workload --------------------
  const baselinesQ = useQuery({
    queryKey: ["baselines", { ns, pod: kind === "Pod" ? name : undefined }],
    queryFn: () => api.listBaselines({ ns, pod: kind === "Pod" ? name : undefined }),
    refetchInterval: 15_000,
    enabled: kind === "Pod",
  });
  const forecastsQ = useQuery({
    queryKey: ["forecasts", { ns, pod: kind === "Pod" ? name : undefined }],
    queryFn: () => api.listForecasts({ ns, pod: kind === "Pod" ? name : undefined }),
    refetchInterval: 15_000,
    enabled: kind === "Pod",
  });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button asChild variant="ghost" size="sm" className="gap-1 px-2">
          <Link to={`/cluster?ns=${encodeURIComponent(ns)}`}>
            <ChevronLeft className="h-4 w-4" />
            {t("workload.back", { defaultValue: "Back to cluster" })}
          </Link>
        </Button>
      </div>

      <WorkloadHeaderCard
        kind={kind}
        namespace={ns}
        name={name}
        pod={pod}
        deployment={deployment}
        incidents={incidents}
        activeCount={activeCount}
      />

      <EventsTimelineRibbon incidents={incidents} />

      <Tabs defaultValue="incidents" className="flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="incidents">
            {t("workload.tabs.incidents", { defaultValue: "Incidents" })}
          </TabsTrigger>
          {kind === "Pod" ? (
            <>
              <TabsTrigger value="baselines">
                {t("workload.tabs.baselines", { defaultValue: "Baselines" })}
              </TabsTrigger>
              <TabsTrigger value="forecasts">
                {t("workload.tabs.forecasts", { defaultValue: "Forecasts" })}
              </TabsTrigger>
            </>
          ) : null}
        </TabsList>

        <TabsContent value="incidents" className="mt-3">
          <Card>
            <CardContent className="p-0">
              {incidentsQ.isLoading ? (
                <p className="p-6 text-sm text-muted-foreground">
                  {t("common.loading", { defaultValue: "Loading…" })}
                </p>
              ) : incidents.length === 0 ? (
                <p className="p-6 text-sm text-muted-foreground">
                  {t("workload.incidents.empty", {
                    defaultValue: "No incidents recorded for this workload.",
                  })}
                </p>
              ) : (
                <IncidentTable items={incidents} />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {kind === "Pod" ? (
          <>
            <TabsContent value="baselines" className="mt-3">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm uppercase tracking-wider">
                    {t("workload.baselines.title", { defaultValue: "Metric baselines" })}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {baselinesQ.isLoading ? (
                    <p className="text-sm text-muted-foreground">
                      {t("common.loading", { defaultValue: "Loading…" })}
                    </p>
                  ) : (baselinesQ.data?.items?.length ?? 0) === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("workload.baselines.empty", {
                        defaultValue:
                          "No baselines yet — the learner needs ~50 samples per metric.",
                      })}
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {(baselinesQ.data?.items ?? []).map((b) => (
                        <li
                          key={`${b.metric}-${b.scope}`}
                          className={cn(
                            "flex items-center gap-3 border-l-2 px-3 py-2 text-xs",
                            b.status === "ok"
                              ? "border-ok"
                              : b.status === "learning"
                                ? "border-info"
                                : "border-muted-foreground/40",
                          )}
                        >
                          <Badge
                            variant={
                              b.status === "ok"
                                ? "ok"
                                : b.status === "learning"
                                  ? "info"
                                  : "secondary"
                            }
                            className="text-[10px] uppercase"
                          >
                            {b.status}
                          </Badge>
                          <span className="flex-1 truncate font-mono">{b.metric}</span>
                          {b.sigma_above != null && b.status === "ok" ? (
                            <span
                              className={cn(
                                "font-mono",
                                b.sigma_above >= 3
                                  ? "text-danger"
                                  : b.sigma_above >= 1
                                    ? "text-warn"
                                    : "text-muted-foreground",
                              )}
                            >
                              {b.sigma_above >= 0 ? "+" : ""}
                              {b.sigma_above.toFixed(1)}σ
                            </span>
                          ) : null}
                          <span className="font-mono text-muted-foreground">
                            n={b.n_samples}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="forecasts" className="mt-3">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm uppercase tracking-wider">
                    {t("workload.forecasts.title", { defaultValue: "Forecast horizons" })}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {forecastsQ.isLoading ? (
                    <p className="text-sm text-muted-foreground">
                      {t("common.loading", { defaultValue: "Loading…" })}
                    </p>
                  ) : (forecastsQ.data?.items?.length ?? 0) === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {t("workload.forecasts.empty", {
                        defaultValue: "No forecasts yet for this workload.",
                      })}
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {(forecastsQ.data?.items ?? []).map((f) => (
                        <li
                          key={`${f.metric}-${f.scope}`}
                          className={cn(
                            "flex items-center gap-3 border-l-2 px-3 py-2 text-xs",
                            f.level === "critical"
                              ? "border-danger"
                              : f.level === "warning"
                                ? "border-warn"
                                : f.level === "watch"
                                  ? "border-info"
                                  : "border-muted-foreground/40",
                          )}
                        >
                          <Badge
                            variant={
                              f.level === "critical"
                                ? "danger"
                                : f.level === "warning"
                                  ? "warn"
                                  : f.level === "watch"
                                    ? "info"
                                    : "secondary"
                            }
                            className="text-[10px] uppercase"
                          >
                            {f.level}
                          </Badge>
                          <span className="flex-1 truncate font-mono">{f.metric}</span>
                          {f.sigma_above != null && f.level !== "none" ? (
                            <span className="font-mono">
                              {f.sigma_above.toFixed(1)}σ · r²={(f.r2 ?? 0).toFixed(2)}
                            </span>
                          ) : (
                            <span className="font-mono text-muted-foreground">—</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </>
        ) : null}
      </Tabs>
    </div>
  );
}

// `OutcomeBadge` + `SeverityChip` + `relativeTimeSeconds` are imported for
// consistency with the existing IncidentTable; the import keeps tree-shaking
// honest in case the table grows new columns from this page later.
void OutcomeBadge;
void SeverityChip;
void relativeTimeSeconds;
