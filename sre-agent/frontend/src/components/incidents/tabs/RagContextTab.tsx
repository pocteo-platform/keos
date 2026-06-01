import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { IncidentDetail } from "@/lib/api";

interface RunbookHit {
  slug?: string;
  text?: string;
  score?: number;
}

export function RagContextTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const hits = (incident.runbook_hits ?? []) as RunbookHit[];

  if (hits.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("incidents.tabs.rag.empty")}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {hits.map((h, idx) => (
        <Card key={h.slug ?? idx}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="font-mono text-sm">{h.slug ?? `hit-${idx + 1}`}</CardTitle>
            {typeof h.score === "number" ? (
              <Badge variant="info">score {h.score.toFixed(3)}</Badge>
            ) : null}
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-xs leading-relaxed">
              {h.text ?? ""}
            </pre>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
