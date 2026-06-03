import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  Briefcase,
  Database,
  HeartPulse,
  Layers,
  Server,
  ServerCog,
  TrendingUp,
} from "lucide-react";
import { HealthSection, type IssueTile, type RiskBucket } from "@/components/reliability/HealthSection";
import { IssuesOverTimeChart } from "@/components/reliability/IssuesOverTimeChart";
import { api } from "@/lib/api";

/**
 * Phase 14.6 — Reliability page.
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Kubernetes Health & Reliability                            │
 *   ├──────────────────────────────────┬─────────────────────────┤
 *   │  WORKLOAD HEALTH                  │  ISSUES OVER TIME       │
 *   │    Realtime: unhealthy pods,      │  (30-day stacked bar:   │
 *   │              degraded deploys,     │   healed/auto/escal/    │
 *   │              forecasted breaches   │   failed per day)       │
 *   │    Risks:    H/M/L counts          │                         │
 *   ├──────────────────────────────────┤                          │
 *   │  INFRASTRUCTURE HEALTH            │                          │
 *   │    Realtime: nodes w/ issues,     │                          │
 *   │              memory pressure,      │                          │
 *   │              control-plane mode    │                          │
 *   │    Risks:    H/M/L counts          │                          │
 *   └──────────────────────────────────┴─────────────────────────┘
 *
 * Data composition is fully frontend; we lean on existing endpoints
 * (pods, deployments, nodes, incidents, active-incidents, forecasts).
 * Severity buckets are derived from incident outcomes so the agent's
 * own decisions inform the risk view — different from Komodor where
 * risks come from static rules.
 */
export default function ReliabilityPage() {
  const { t } = useTranslation();

  const podsAllNs = useQuery({
    queryKey: ["pods", "all"],
    queryFn: () => api.listPods(),
    refetchInterval: 8_000,
  });
  const nodesQ = useQuery({
    queryKey: ["nodes"],
    queryFn: api.listNodes,
    refetchInterval: 10_000,
  });
  const incidentsQ = useQuery({
    queryKey: ["incidents", { limit: 200 }],
    queryFn: () => api.listIncidents({ limit: 200 }),
    refetchInterval: 10_000,
  });
  const activeQ = useQuery({
    queryKey: ["active-incidents"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 4_000,
  });
  const forecastsQ = useQuery({
    queryKey: ["forecasts", "all"],
    queryFn: () => api.listForecasts({}),
    refetchInterval: 15_000,
  });
  const modeQ = useQuery({ queryKey: ["mode"], queryFn: api.getMode });

  // ----- Workload counts ----------------------------------------------
  const pods = podsAllNs.data ?? [];
  const unhealthyPods = pods.filter((p) => p.phase !== "Running" && p.phase !== "Succeeded");
  const podsWithRestarts = pods.filter((p) => p.restarts > 0);
  const forecasts = forecastsQ.data?.items ?? [];
  const forecastedBreaches = forecasts.filter(
    (f) => f.level === "warning" || f.level === "critical",
  ).length;

  // ----- Workload risks (severity-bucketed from incident history) -----
  const incidents = incidentsQ.data?.items ?? [];
  const last7d = useMemo(() => {
    const cutoff = Date.now() - 7 * 86400 * 1000;
    return incidents.filter((i) => {
      const ts = new Date(i.ts).getTime();
      return Number.isFinite(ts) && ts >= cutoff;
    });
  }, [incidents]);
  // Pod-scoped incidents → "Degraded services" risk bucket
  const degradedServices: RiskBucket = useMemo(() => {
    const h = last7d.filter(
      (i) => i.namespace !== "kube-system" && i.severity === "critical",
    ).length;
    const m = last7d.filter(
      (i) => i.namespace !== "kube-system" && i.severity === "high",
    ).length;
    const l = last7d.filter(
      (i) =>
        i.namespace !== "kube-system" &&
        (i.severity === "medium" || i.severity === "low"),
    ).length;
    return {
      key: "degradedServices",
      defaultLabel: "Degraded services",
      high: h,
      medium: m,
      low: l,
    };
  }, [last7d]);

  // ----- Infrastructure counts ---------------------------------------
  // NodeSummary in the agent's projection only exposes `ready` + `taints`.
  // Pressure-condition surfaces in the K8s API only land here via taints
  // (`node.kubernetes.io/memory-pressure`, `.../disk-pressure`,
  // `.../pid-pressure`, `.../not-ready`). We derive pressure counts from
  // those taint keys — cheaper than fanning out per-node describe calls
  // and accurate for the operator-facing summary.
  const nodes = nodesQ.data ?? [];
  const hasPressureTaint = (n: { taints: string[] }, key: string) =>
    n.taints.some((tt) => tt.startsWith(key));
  const nodesNotReady = nodes.filter((n) => !n.ready).length;
  const nodesMemPressure = nodes.filter((n) =>
    hasPressureTaint(n, "node.kubernetes.io/memory-pressure"),
  ).length;
  const nodesDiskPressure = nodes.filter((n) =>
    hasPressureTaint(n, "node.kubernetes.io/disk-pressure"),
  ).length;
  const nodesPidPressure = nodes.filter((n) =>
    hasPressureTaint(n, "node.kubernetes.io/pid-pressure"),
  ).length;
  const nodesWithPressure = nodesMemPressure + nodesDiskPressure + nodesPidPressure;
  const nodePressureRisk: RiskBucket = {
    key: "nodePressure",
    defaultLabel: "Node pressure",
    high: nodesMemPressure + nodesDiskPressure,
    medium: nodesPidPressure,
    low: nodes.filter(
      (n) => !n.ready && !hasPressureTaint(n, "node.kubernetes.io/memory-pressure"),
    ).length,
  };

  // ----- Tile definitions --------------------------------------------
  const workloadTiles: IssueTile[] = [
    {
      key: "unhealthyServices",
      defaultLabel: "Unhealthy pods",
      value: unhealthyPods.length,
      icon: <Briefcase className="h-4 w-4" />,
      variant: unhealthyPods.length === 0 ? "ok" : "warn",
      hint:
        unhealthyPods.length === 0
          ? t("reliability.tiles.allHealthy", { defaultValue: "All running" })
          : t("reliability.tiles.unhealthyHint", {
              count: new Set(unhealthyPods.map((p) => p.namespace)).size,
              defaultValue_one: "across {{count}} namespace",
              defaultValue_other: "across {{count}} namespaces",
            }),
    },
    {
      key: "activeIncidents",
      defaultLabel: "Active incidents",
      value: activeQ.data?.length ?? 0,
      icon: <HeartPulse className="h-4 w-4" />,
      variant: (activeQ.data?.length ?? 0) > 0 ? "warn" : "ok",
    },
    {
      key: "forecastedBreaches",
      defaultLabel: "Forecasted breaches",
      value: forecastedBreaches,
      icon: <TrendingUp className="h-4 w-4" />,
      variant: forecastedBreaches > 0 ? "warn" : "ok",
      hint:
        forecastedBreaches === 0
          ? t("reliability.tiles.noSurge", { defaultValue: "No surge predicted" })
          : t("reliability.tiles.surgeWindow", { defaultValue: "5-min horizon" }),
    },
    {
      key: "podRestarts",
      defaultLabel: "Pods restarted",
      value: podsWithRestarts.length,
      icon: <Layers className="h-4 w-4" />,
      variant: podsWithRestarts.length > 5 ? "warn" : "default",
      hint: t("reliability.tiles.restartsHint", { defaultValue: "this run" }),
    },
  ];

  const infraTiles: IssueTile[] = [
    {
      key: "nodesNotReady",
      defaultLabel: "Nodes not Ready",
      value: nodesNotReady,
      icon: <Server className="h-4 w-4" />,
      variant: nodesNotReady === 0 ? "ok" : "danger",
    },
    {
      key: "nodesWithPressure",
      defaultLabel: "Nodes with pressure",
      value: nodesWithPressure,
      icon: <ServerCog className="h-4 w-4" />,
      variant: nodesWithPressure === 0 ? "ok" : "warn",
      hint: t("reliability.tiles.pressureHint", {
        defaultValue: "memory / disk / PID",
      }),
    },
    {
      key: "agentMode",
      defaultLabel: "Agent mode",
      value: modeQ.data?.mode ?? "—",
      icon: <AlertOctagon className="h-4 w-4" />,
      variant:
        modeQ.data?.mode === "active"
          ? "ok"
          : modeQ.data?.mode === "frozen"
            ? "danger"
            : "info",
    },
    {
      key: "baselinesKnown",
      defaultLabel: "Baselines known",
      value: forecasts.length,
      icon: <Database className="h-4 w-4" />,
      variant: forecasts.length > 0 ? "info" : "default",
      hint: t("reliability.tiles.baselinesHint", {
        defaultValue: "(metric, ns, pod) tuples",
      }),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("reliability.title", { defaultValue: "Kubernetes health & reliability" })}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("reliability.subtitle", {
            defaultValue:
              "Workload and infrastructure risks, ranked by the agent's view of the cluster.",
          })}
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_28rem]">
        <div className="flex flex-col gap-4">
          <HealthSection
            title={t("reliability.workload.title", { defaultValue: "Workload health" })}
            realtime={workloadTiles}
            risks={[degradedServices]}
          />
          <HealthSection
            title={t("reliability.infra.title", { defaultValue: "Infrastructure health" })}
            realtime={infraTiles}
            risks={[nodePressureRisk]}
          />
        </div>
        <IssuesOverTimeChart incidents={incidents} windowDays={30} />
      </div>
    </div>
  );
}
