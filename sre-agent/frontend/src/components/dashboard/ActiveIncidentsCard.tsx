import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Radio } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

/**
 * Live list of in-flight incidents. Polls /api/incidents/active
 * every 3 s. Each row is a link into the live pipeline page.
 */
export function ActiveIncidentsCard() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["active-incidents"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 3_000,
  });
  const items = q.data ?? [];
  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <Radio className="h-4 w-4 text-info" />
          {t("dashboard.active.title", { defaultValue: "Active incidents" })}
          {items.length ? (
            <Badge variant="info" className="ml-auto">
              {items.length}
            </Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("dashboard.active.empty", {
              defaultValue: "No incidents in flight — the agent is idle.",
            })}
          </p>
        ) : (
          <ul className="space-y-2">
            {items.slice(0, 6).map((inc) => (
              <li key={inc.incident_id}>
                <Link
                  to={`/pipeline/${inc.incident_id}`}
                  className="flex items-center gap-3 border-l-2 border-info bg-info/5 px-3 py-2 hover:bg-info/10"
                >
                  <span className="h-2 w-2 animate-node-pulse bg-info" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <Badge
                        variant={
                          inc.severity === "critical"
                            ? "danger"
                            : inc.severity === "high"
                              ? "warn"
                              : "secondary"
                        }
                        className="text-[10px] uppercase"
                      >
                        {inc.severity ?? "?"}
                      </Badge>
                      <span className="truncate font-mono text-xs">
                        {inc.namespace ?? "?"}/{inc.pod ?? "?"}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-foreground/90">{inc.rule ?? "—"}</p>
                  </div>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {inc.incident_id.slice(0, 8)}…
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
