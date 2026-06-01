import { HmacChainVerifier } from "../HmacChainVerifier";
import type { IncidentDetail } from "@/lib/api";

export function AuditChainTab({ incident }: { incident: IncidentDetail }) {
  return <HmacChainVerifier incidentId={incident.incident_id} />;
}
