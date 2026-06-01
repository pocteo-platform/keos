import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonBlock } from "@/components/ui/json-block";
import type { IncidentDetail } from "@/lib/api";

export function PolicyDecisionTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const pd = (incident.policy_decision ?? {}) as {
    allow?: boolean;
    reasons?: string[];
    evaluated_with_mode?: string;
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm">{t("incidents.tabs.policy.decision")}</CardTitle>
          {pd.allow === true ? (
            <Badge variant="ok">ALLOW</Badge>
          ) : pd.allow === false ? (
            <Badge variant="danger">DENY</Badge>
          ) : (
            <Badge variant="secondary">—</Badge>
          )}
        </CardHeader>
        <CardContent>
          {pd.evaluated_with_mode ? (
            <p className="text-xs text-muted-foreground">
              {t("incidents.tabs.policy.modeAtEval")}:{" "}
              <span className="font-mono">{pd.evaluated_with_mode}</span>
            </p>
          ) : null}
          {pd.reasons?.length ? (
            <ul className="mt-3 space-y-1 text-sm">
              {pd.reasons.map((r) => (
                <li key={r} className="rounded border border-warn/40 bg-warn/10 p-2">
                  {r}
                </li>
              ))}
            </ul>
          ) : pd.allow === true ? (
            <p className="mt-3 text-sm text-muted-foreground">
              {t("incidents.tabs.policy.noReasons")}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t("incidents.tabs.policy.raw")}</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonBlock data={incident.policy_decision ?? {}} maxHeightRem={20} />
        </CardContent>
      </Card>
    </div>
  );
}
