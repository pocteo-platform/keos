import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { IncidentDetail } from "@/lib/api";

interface Finding {
  kind?: string;
  name?: string;
  error?: string;
  parent_object?: string | null;
  details?: string;
}

export function K8sgptFindingsTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const findings = (incident.k8sgpt_findings ?? []) as Finding[];

  if (findings.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("incidents.tabs.k8sgpt.empty")}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {findings.map((f, idx) => (
        <Card key={`${f.name ?? idx}-${idx}`}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Badge variant="info">{f.kind ?? "?"}</Badge>
              <span className="font-mono">{f.name ?? "?"}</span>
            </CardTitle>
            {f.parent_object ? (
              <span className="font-mono text-xs text-muted-foreground">{f.parent_object}</span>
            ) : null}
          </CardHeader>
          <CardContent>
            <p className="text-sm">{f.error ?? ""}</p>
            {f.details ? <p className="mt-2 text-xs text-muted-foreground">{f.details}</p> : null}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
