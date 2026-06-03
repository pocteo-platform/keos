import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { OutcomeBanner } from "./OutcomeBanner";
import { ResourcesAffectedList } from "./ResourcesAffectedList";
import { ActionChainStepper } from "./ActionChainStepper";
import type { PipelineState } from "@/hooks/usePipelineState";

/** "Graph terminated" derivation. The PipelineState reducer doesn't
 *  carry an explicit `done` flag — we infer terminality from the
 *  two outgoing edges of the graph: `verify` finishing OR
 *  `escalate` finishing. Both are valid terminal states for one
 *  incident; whichever lands first locks the scene refetch. */
function isTerminal(state: PipelineState): boolean {
  const v = state.nodes.verify?.status;
  const e = state.nodes.escalate?.status;
  return v === "done" || v === "error" || e === "done" || e === "error";
}

/**
 * The "scene" pane for the live pipeline + replay views — the answer
 * to "what did the agent DO during this incident, and how does that
 * map to actual cluster objects?".
 *
 * Three stacked sections:
 *   1. OutcomeBanner          — big visual healed/paged/failed answer
 *   2. ResourcesAffectedList  — every K8s object the agent touched
 *   3. ActionChainStepper     — the graph topology with per-step
 *                                status + one-line summary
 *
 * Data source: `GET /api/scene/:id`. We poll on a 2s cadence while
 * the pipeline state's `state.done` flag is false; otherwise stop
 * polling (the persisted snapshot doesn't change once the graph
 * terminates). When `state.done` flips, we refetch once to make
 * sure the banner reflects the terminal verdict.
 */

interface Props {
  incidentId: string | null;
  state: PipelineState;
}

export function ScenePane({ incidentId, state }: Props) {
  const { t } = useTranslation();
  const done = useMemo(() => isTerminal(state), [state]);
  const sceneQ = useQuery({
    queryKey: ["scene", incidentId],
    queryFn: () => (incidentId ? api.getScene(incidentId) : Promise.resolve(null)),
    enabled: !!incidentId,
    // Live polling while the graph is still running, then stop.
    refetchInterval: done ? false : 2000,
    // The first few polls will 404 (the incident isn't persisted yet)
    // — that's expected; don't retry-flood the API.
    retry: 1,
  });

  // When terminality flips true, do one final refetch so the
  // OutcomeBanner switches from `unknown` (graph in flight) to the
  // terminal verdict the persisted incident now carries.
  useEffect(() => {
    if (done && incidentId) {
      void sceneQ.refetch();
    }
    // We only want to retrigger on the `done` edge, not on every
    // re-render — refetchInterval handles the rest.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, incidentId]);

  if (!incidentId) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("scene.idle", {
          defaultValue: "No incident selected — pick one or run a use case to populate the scene.",
        })}
      </div>
    );
  }

  if (sceneQ.isLoading && !sceneQ.data) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t("scene.loading", { defaultValue: "Loading scene…" })}
      </div>
    );
  }

  if (sceneQ.isError && !sceneQ.data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
        <p>
          {t("scene.waiting", {
            defaultValue: "Waiting for the agent to finish — the scene is empty until then.",
          })}
        </p>
      </div>
    );
  }

  const scene = sceneQ.data;
  if (!scene) return null;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <OutcomeBanner outcome={scene.outcome} hideUntilTerminal={!done} />
      <ResourcesAffectedList resources={scene.resources} />
      <ActionChainStepper steps={scene.steps} />
    </div>
  );
}
