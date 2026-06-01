import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Box, Cpu, Layers, ShieldCheck } from "lucide-react";
import { api, type PodSummary } from "@/lib/api";
import { useChannel } from "@/lib/ws";
import { NamespacePicker } from "@/components/kube/NamespacePicker";
import { PodCard } from "@/components/kube/PodCard";
import { PodDetailDrawer } from "@/components/kube/PodDetailDrawer";
import { ModeBadge } from "@/components/kube/ModeBadge";
import { NodesTable } from "@/components/kube/NodesTable";
import { DeploymentsTable } from "@/components/kube/DeploymentsTable";
import { EventsList } from "@/components/kube/EventsList";

function StatCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{label}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  );
}

export default function ClusterPage() {
  const { t } = useTranslation();
  const [ns, setNs] = useState<string>("workloads");
  const [openPod, setOpenPod] = useState<PodSummary | null>(null);
  const [recentlyChanged, setRecentlyChanged] = useState<Record<string, number>>({});

  const nodesQ = useQuery({ queryKey: ["nodes"], queryFn: api.listNodes });
  const nsQ = useQuery({ queryKey: ["namespaces"], queryFn: api.listNamespaces });
  const modeQ = useQuery({ queryKey: ["mode"], queryFn: api.getMode });
  const podsQ = useQuery({
    queryKey: ["pods", ns],
    queryFn: () => api.listPods(ns),
    refetchInterval: 4000,
  });

  useChannel("cluster", (event) => {
    const payload = event as { pods?: PodSummary[] };
    if (payload.pods) {
      const prev = podsQ.data ?? [];
      const prevMap = new Map(prev.map((p) => [p.name, p]));
      const now = Date.now();
      const flashes: Record<string, number> = {};
      for (const p of payload.pods) {
        const before = prevMap.get(p.name);
        if (!before || before.restarts !== p.restarts || before.phase !== p.phase) {
          flashes[p.name] = now;
        }
      }
      if (Object.keys(flashes).length) {
        setRecentlyChanged((s) => ({ ...s, ...flashes }));
      }
    }
  });

  useEffect(() => {
    if (!Object.keys(recentlyChanged).length) return;
    const t = window.setTimeout(() => setRecentlyChanged({}), 2000);
    return () => window.clearTimeout(t);
  }, [recentlyChanged]);

  const runningPods = useMemo(
    () => (podsQ.data ?? []).filter((p) => p.phase === "Running").length,
    [podsQ.data],
  );
  const allNamespaces = useMemo(() => (nsQ.data ?? []).map((n) => n.name).sort(), [nsQ.data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("cluster.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("cluster.subtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label={t("cluster.stats.nodes")} value={nodesQ.data?.length ?? "—"} icon={Cpu} />
        <StatCard
          label={t("cluster.stats.namespaces")}
          value={nsQ.data?.length ?? "—"}
          icon={Layers}
        />
        <StatCard label={t("cluster.stats.runningPods")} value={runningPods} icon={Box} />
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t("cluster.stats.agentMode")}</CardTitle>
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <ModeBadge mode={modeQ.data?.mode} />
          </CardContent>
        </Card>
      </div>

      <NamespacePicker
        value={ns}
        onChange={setNs}
        namespaces={allNamespaces.length ? allNamespaces : [ns]}
      />

      <Tabs defaultValue="pods" className="w-full">
        <TabsList>
          <TabsTrigger value="pods">{t("cluster.tabs.pods")}</TabsTrigger>
          <TabsTrigger value="deployments">{t("cluster.tabs.deployments")}</TabsTrigger>
          <TabsTrigger value="nodes">{t("cluster.tabs.nodes")}</TabsTrigger>
          <TabsTrigger value="events">{t("cluster.tabs.events")}</TabsTrigger>
        </TabsList>

        <TabsContent value="pods">
          {podsQ.isLoading ? (
            <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
          ) : (podsQ.data?.length ?? 0) === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <h2 className="font-semibold">{t("cluster.empty.title")}</h2>
                <p className="text-sm text-muted-foreground">{t("cluster.empty.body")}</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {(podsQ.data ?? []).map((pod) => (
                <PodCard
                  key={pod.name}
                  pod={pod}
                  recentlyChanged={Boolean(recentlyChanged[pod.name])}
                  onClick={(p) => setOpenPod(p)}
                />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="deployments">
          <DeploymentsTable ns={ns} />
        </TabsContent>

        <TabsContent value="nodes">
          <NodesTable />
        </TabsContent>

        <TabsContent value="events">
          <EventsList ns={ns} />
        </TabsContent>
      </Tabs>

      <PodDetailDrawer
        pod={openPod}
        open={Boolean(openPod)}
        onOpenChange={(o) => !o && setOpenPod(null)}
      />
    </div>
  );
}
