import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { IncidentTable } from "@/components/incidents/IncidentTable";
import { api } from "@/lib/api";

export default function IncidentsPage() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["incidents", "list"],
    queryFn: () => api.listIncidents({ limit: 500 }),
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">{t("incidents.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("incidents.subtitle")}</p>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : (q.data?.items.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {t("incidents.empty")}
          </CardContent>
        </Card>
      ) : (
        <IncidentTable items={q.data?.items ?? []} />
      )}
    </div>
  );
}
