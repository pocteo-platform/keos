import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import { PipelineCanvas } from "@/components/pipeline/PipelineCanvas";
import { usePipelineState, type TraceEvent } from "@/hooks/usePipelineState";
import type { IncidentDetail } from "@/lib/api";

/** Pre-applies the whole trace and shows the final-state canvas. Replay
 *  scrubber lives at /pipeline/:id; embedding it here would duplicate the
 *  Pipeline route inside the Incidents detail. */
export function PipelineTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const { state, apply, reset } = usePipelineState();

  const trace = useMemo<TraceEvent[]>(() => {
    return ((incident.pipeline_trace ?? []) as unknown as TraceEvent[])
      .slice()
      .sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
  }, [incident.pipeline_trace]);

  useEffect(() => {
    reset();
    for (const ev of trace) apply(ev);
  }, [trace, apply, reset]);

  if (trace.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("incidents.tabs.pipeline.empty")}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="h-[36rem] p-0">
        <PipelineCanvas state={state} onSelectNode={() => undefined} />
      </CardContent>
    </Card>
  );
}
