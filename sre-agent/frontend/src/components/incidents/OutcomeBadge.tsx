import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { IncidentSummary } from "@/lib/api";

export function OutcomeBadge({ incident }: { incident: IncidentSummary }) {
  const { t } = useTranslation();
  // Stale-resolved incidents short-circuited at triage because the
  // underlying problem already healed itself. Render distinctly so the
  // operator doesn't think a human needs to act.
  if (incident.stale_resolved) {
    return (
      <Badge
        variant="info"
        title={t("incidents.outcomes.autoResolvedHint", {
          defaultValue: "Event was stale and the target was already Ready. No action taken.",
        })}
      >
        {t("incidents.outcomes.autoResolved", { defaultValue: "Auto-resolved" })}
      </Badge>
    );
  }
  if (incident.escalated) {
    return <Badge variant="warn">{t("incidents.outcomes.escalated")}</Badge>;
  }
  if (incident.healed === true) {
    return <Badge variant="ok">{t("incidents.outcomes.healed")}</Badge>;
  }
  if (incident.healed === false) {
    return <Badge variant="danger">{t("incidents.outcomes.failed")}</Badge>;
  }
  return <Badge variant="secondary">{t("incidents.outcomes.unknown")}</Badge>;
}
