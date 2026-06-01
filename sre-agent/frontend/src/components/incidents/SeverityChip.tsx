import { Badge } from "@/components/ui/badge";
import type { IncidentSummary } from "@/lib/api";

type Variant = "ok" | "info" | "warn" | "danger";

const map: Record<IncidentSummary["severity"], Variant> = {
  low: "info",
  medium: "warn",
  high: "danger",
  critical: "danger",
};

export function SeverityChip({ severity }: { severity: IncidentSummary["severity"] }) {
  return <Badge variant={map[severity]}>{severity}</Badge>;
}
