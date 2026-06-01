import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import { JsonBlock } from "@/components/ui/json-block";
import type { IncidentDetail } from "@/lib/api";

export function FalcoEventTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  if (!incident.raw_event) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("incidents.tabs.falco.empty")}
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="pt-6">
        <JsonBlock data={incident.raw_event} maxHeightRem={32} />
      </CardContent>
    </Card>
  );
}
