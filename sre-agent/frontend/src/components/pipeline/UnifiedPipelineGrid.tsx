import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { PipelineCanvas } from "./PipelineCanvas";
import { IncidentTimeline } from "./IncidentTimeline";
import { TelemetrySidebar } from "./TelemetrySidebar";
import { OutcomeBanner } from "./OutcomeBanner";
import { ResourcesAffectedList } from "./ResourcesAffectedList";
import { ActionChainStepper } from "./ActionChainStepper";
import type { PipelineState } from "@/hooks/usePipelineState";
import type { NodeKind } from "@/lib/pipeline-topology";
import type { Domain } from "@/lib/branding";

/**
 * Komodor-pro unified layout — replaces the Phase 14.5a tab UX.
 * Everything an operator might want during a use-case run is on
 * one page:
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  OutcomeBanner — the verdict at the top                  │
 *   ├──────────────────────────────┬───────────────────────────┤
 *   │                               │  TelemetrySidebar         │
 *   │                               ├───────────────────────────┤
 *   │       PipelineCanvas          │  ResourcesAffectedList    │
 *   │       (React Flow graph)      ├───────────────────────────┤
 *   │                               │  ActionChainStepper       │
 *   │                               │                           │
 *   ├───────────────────────────────┴───────────────────────────┤
 *   │  IncidentTimeline — vertical Observe→Predict→Heal→Learn   │
 *   └──────────────────────────────────────────────────────────┘
 *
 * The scene data is fetched here once and threaded down into the
 * three scene sub-components so we don't multi-poll /api/scene.
 * Each section gracefully degrades (no data → empty state) so the
 * grid stays stable while the agent runs.
 */

interface Props {
  state: PipelineState;
  incidentId: string;
  onSelectNode: (k: NodeKind) => void;
  // Phase 21 — "security" re-labels the timeline phases. Defaults to "sre".
  domain?: Domain;
}

function isTerminal(state: PipelineState): boolean {
  const v = state.nodes.verify?.status;
  const e = state.nodes.escalate?.status;
  return v === "done" || v === "error" || e === "done" || e === "error";
}

export function UnifiedPipelineGrid({ state, incidentId, onSelectNode, domain = "sre" }: Props) {
  const { t } = useTranslation();
  const done = useMemo(() => isTerminal(state), [state]);

  // Single scene poll, shared by OutcomeBanner + ResourcesAffectedList
  // + ActionChainStepper. Polls every 2 s while the graph is in
  // flight; locks once terminality flips. The first few polls 404
  // (incident not yet persisted) — that's expected; quiet retry.
  const sceneQ = useQuery({
    queryKey: ["scene", incidentId],
    queryFn: () => api.getScene(incidentId),
    refetchInterval: done ? false : 2000,
    retry: 1,
  });
  const scene = sceneQ.data ?? null;

  return (
    <div className="flex flex-1 flex-col gap-4">
      {/* Outcome banner sits ABOVE the grid — the answer the operator
          is looking for. Animates from "Running…" placeholder to the
          terminal verdict as soon as the persisted incident lands. */}
      <OutcomeBanner outcome={scene?.outcome ?? null} hideUntilTerminal={!done} />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_24rem]">
        {/* Left column: React Flow canvas. The "wow" surface where
            the operator watches the graph animate node by node. */}
        <Card className="min-h-[28rem] xl:min-h-[34rem]">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm uppercase tracking-wider">
              {t("pipeline.unified.graphTitle", { defaultValue: "Agent pipeline" })}
            </CardTitle>
          </CardHeader>
          <CardContent className="h-[calc(100%-3rem)] p-0">
            <PipelineCanvas state={state} onSelectNode={onSelectNode} />
          </CardContent>
        </Card>

        {/* Right column: stacked context. TelemetrySidebar already
            renders its own Card chrome; the two scene cards do too. */}
        <div className="flex flex-col gap-4">
          <TelemetrySidebar state={state} />
          <ResourcesAffectedList resources={scene?.resources ?? []} />
          <ActionChainStepper steps={scene?.steps ?? []} />
        </div>
      </div>

      {/* Full-width vertical timeline — the chronological view that
          mirrors Komodor's "events" timeline ribbon. Stays below
          the graph so the operator can scroll once they want to
          see *when* each node ran in absolute terms. */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm uppercase tracking-wider">
            {domain === "security"
              ? t("pipeline.unified.timelineTitleSecurity", {
                  defaultValue: "Detect → Investigate → Contain → Respond",
                })
              : t("pipeline.unified.timelineTitle", {
                  defaultValue: "Observe → Predict → Heal → Learn",
                })}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          <IncidentTimeline state={state} onSelectNode={onSelectNode} domain={domain} />
        </CardContent>
      </Card>
    </div>
  );
}
