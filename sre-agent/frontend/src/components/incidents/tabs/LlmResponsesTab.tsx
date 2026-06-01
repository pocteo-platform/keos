import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonBlock } from "@/components/ui/json-block";
import type { IncidentDetail } from "@/lib/api";

/** Surfaces the structured LLM outputs (diagnose + plan_action). We don't
 *  ship raw token streams in the trace — that would balloon the audit
 *  payload — so this tab focuses on the validated JSON the agent acted on,
 *  with the timing chips from the pipeline trace. */
export function LlmResponsesTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const trace = (incident.pipeline_trace ?? []) as Array<{
    node?: string;
    phase?: string;
    elapsed_ms?: number;
  }>;

  const diagnoseEnd = trace.find((r) => r.node === "diagnose" && r.phase === "end");
  const planEnd = trace.find((r) => r.node === "plan_action" && r.phase === "end");

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm">{t("incidents.tabs.llm.diagnose")}</CardTitle>
          {diagnoseEnd ? <Badge variant="secondary">{diagnoseEnd.elapsed_ms ?? 0}ms</Badge> : null}
        </CardHeader>
        <CardContent>
          {incident.diagnosis ? (
            <JsonBlock data={incident.diagnosis} maxHeightRem={20} />
          ) : (
            <p className="text-sm text-muted-foreground">{t("common.none")}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm">{t("incidents.tabs.llm.plan")}</CardTitle>
          {planEnd ? <Badge variant="secondary">{planEnd.elapsed_ms ?? 0}ms</Badge> : null}
        </CardHeader>
        <CardContent>
          {incident.proposed_action ? (
            <JsonBlock data={incident.proposed_action} maxHeightRem={20} />
          ) : (
            <p className="text-sm text-muted-foreground">{t("common.none")}</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
