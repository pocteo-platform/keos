import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, PlayCircle } from "lucide-react";
import { format } from "date-fns";
import { enUS, fr as frLocale } from "date-fns/locale";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SeverityChip } from "@/components/incidents/SeverityChip";
import { OutcomeBadge } from "@/components/incidents/OutcomeBadge";
import { ModeBadge } from "@/components/kube/ModeBadge";
import { PipelineTab } from "@/components/incidents/tabs/PipelineTab";
import { FalcoEventTab } from "@/components/incidents/tabs/FalcoEventTab";
import { RagContextTab } from "@/components/incidents/tabs/RagContextTab";
import { K8sgptFindingsTab } from "@/components/incidents/tabs/K8sgptFindingsTab";
import { LlmResponsesTab } from "@/components/incidents/tabs/LlmResponsesTab";
import { PolicyDecisionTab } from "@/components/incidents/tabs/PolicyDecisionTab";
import { ActionResultTab } from "@/components/incidents/tabs/ActionResultTab";
import { AuditChainTab } from "@/components/incidents/tabs/AuditChainTab";
import { api, type IncidentDetail } from "@/lib/api";
import { useApp } from "@/lib/store";
import { useChannel } from "@/lib/ws";

/** Detail page with real-time updates:
 *
 *  - Subscribes to `pipeline.<id>` and accumulates trace events locally,
 *    so the Pipeline tab animates as the agent runs.
 *  - Subscribes to `audit` and refetches the per-incident audit chain
 *    whenever a record for THIS incident lands.
 *  - Refetches the full incident every 5s while it's still in flight
 *    (no `complete` audit yet), and stops polling once it lands.
 */
export default function IncidentDetailPage() {
  const { t } = useTranslation();
  const params = useParams<{ id: string }>();
  const id = params.id!;
  const locale = useApp((s) => s.locale);
  const dfLocale = locale === "fr" ? frLocale : enUS;
  const qc = useQueryClient();

  // Local mirror of trace events received over the WS while the user is on
  // this page. Merged with the server-fetched trace below.
  const [liveTrace, setLiveTrace] = useState<Record<string, unknown>[]>([]);
  const completeRef = useRef(false);

  const q = useQuery({
    queryKey: ["incident", id],
    queryFn: () => api.getIncident(id),
    // Poll the full incident every 4s while it might still be in flight.
    // The condition flips on `complete` arrival (set in the WS handler).
    refetchInterval: () => (completeRef.current ? false : 4000),
  });

  // Live trace events. We dedupe by (ts, node, phase) so reapplying never
  // double-counts an event that's also in the server-fetched trace.
  useChannel(`pipeline.${id}`, (event) => {
    setLiveTrace((prev) => {
      const key = `${event.ts}-${event.node}-${event.phase}`;
      if (prev.some((p) => `${p.ts}-${p.node}-${p.phase}` === key)) return prev;
      return [...prev, event];
    });
    // When `complete` lands, stop polling and refetch one last time.
    if (event.phase === "end" && event.node === "verify") {
      // Heuristic: verify-end OR escalate-end is the last node.
      void qc.invalidateQueries({ queryKey: ["incident", id] });
    }
  });

  // Audit channel: any record carrying our incident_id → invalidate the
  // per-incident audit query so the chain tab refreshes.
  useChannel("audit", (event) => {
    const rec = event as { incident_id?: string; event_type?: string };
    if (rec.incident_id !== id) return;
    void qc.invalidateQueries({ queryKey: ["audit", "for-incident", id] });
    if (rec.event_type === "complete") {
      completeRef.current = true;
      void qc.invalidateQueries({ queryKey: ["incident", id] });
    }
  });

  // Merged incident: take the server's snapshot, then overlay the live
  // trace events the server hasn't recorded yet.
  const merged = useMemo<IncidentDetail | undefined>(() => {
    if (!q.data) return undefined;
    const serverTrace = q.data.pipeline_trace ?? [];
    const known = new Set(
      serverTrace.map(
        (r) =>
          `${(r as Record<string, unknown>).ts}-${(r as Record<string, unknown>).node}-${(r as Record<string, unknown>).phase}`,
      ),
    );
    const extras = liveTrace.filter((r) => !known.has(`${r.ts}-${r.node}-${r.phase}`));
    return { ...q.data, pipeline_trace: [...serverTrace, ...extras] };
  }, [q.data, liveTrace]);

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">{t("common.loading")}</p>;
  }
  if (q.isError || !merged) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("errors.fetchFailed")}
        </CardContent>
      </Card>
    );
  }
  const inc = merged;
  const isLive = !completeRef.current && inc.healed === null && !inc.escalated;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/incidents">
            <ChevronLeft className="h-4 w-4" />
            {t("common.back")}
          </Link>
        </Button>
        <Button asChild variant="outline" size="sm">
          <Link to={`/pipeline/${inc.incident_id}`}>
            <PlayCircle className="h-4 w-4" />
            {t("incidents.actions.replay", { defaultValue: "Replay in pipeline" })}
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3 font-mono text-base">
            {inc.incident_id}
            <SeverityChip severity={inc.severity} />
            <OutcomeBadge incident={inc} />
            <ModeBadge mode={inc.agent_mode} />
            {isLive ? (
              <Badge variant="info" className="gap-1">
                <span className="h-2 w-2 animate-node-pulse rounded-full bg-info" />
                {t("topbar.live")}
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            {inc.ts ? format(new Date(inc.ts), "PPPp", { locale: dfLocale }) : "—"} ·{" "}
            <span className="font-mono">{inc.namespace ?? "—"}</span> /{" "}
            <span className="font-mono">{inc.pod ?? "—"}</span>
            {inc.rule ? <> · {inc.rule}</> : null}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {inc.diagnosis_summary ? (
            <p className="text-sm">{inc.diagnosis_summary}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("common.none")}</p>
          )}
          {inc.proposed_tool ? (
            <div className="mt-3 flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">{t("incidents.detail.proposed")}</span>
              <Badge variant="secondary">{inc.proposed_tool}</Badge>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Tabs defaultValue="pipeline" className="w-full">
        <TabsList className="flex w-full flex-wrap">
          <TabsTrigger value="pipeline">{t("incidents.tabs.pipeline.title")}</TabsTrigger>
          <TabsTrigger value="falco">{t("incidents.tabs.falco.title")}</TabsTrigger>
          <TabsTrigger value="rag">{t("incidents.tabs.rag.title")}</TabsTrigger>
          <TabsTrigger value="k8sgpt">{t("incidents.tabs.k8sgpt.title")}</TabsTrigger>
          <TabsTrigger value="llm">{t("incidents.tabs.llm.title")}</TabsTrigger>
          <TabsTrigger value="policy">{t("incidents.tabs.policy.title")}</TabsTrigger>
          <TabsTrigger value="action">{t("incidents.tabs.action.title")}</TabsTrigger>
          <TabsTrigger value="audit">{t("incidents.tabs.audit.title")}</TabsTrigger>
        </TabsList>

        <TabsContent value="pipeline">
          <PipelineTab incident={inc} />
        </TabsContent>
        <TabsContent value="falco">
          <FalcoEventTab incident={inc} />
        </TabsContent>
        <TabsContent value="rag">
          <RagContextTab incident={inc} />
        </TabsContent>
        <TabsContent value="k8sgpt">
          <K8sgptFindingsTab incident={inc} />
        </TabsContent>
        <TabsContent value="llm">
          <LlmResponsesTab incident={inc} />
        </TabsContent>
        <TabsContent value="policy">
          <PolicyDecisionTab incident={inc} />
        </TabsContent>
        <TabsContent value="action">
          <ActionResultTab incident={inc} />
        </TabsContent>
        <TabsContent value="audit">
          <AuditChainTab incident={inc} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
