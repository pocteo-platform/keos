import { useTranslation } from "react-i18next";
import { motion } from "motion/react";
import { AlertTriangle, Box, Cpu, GitBranch, History, Image as ImageIcon, Server } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn, relativeTimeSeconds } from "@/lib/utils";
import type { IncidentSummary, PodSummary, DeploymentSummary } from "@/lib/api";

/**
 * Komodor-inspired workload header card. The chips row at the top
 * of the drill-down view, telling the operator everything they
 * need to know about THIS workload in two seconds:
 *
 *   Type / Namespace / Image / Node (pod) or Replicas (deployment)
 *   Health · Duration · Last action · Open incidents
 *
 * Driven by 3 data sources (composed in the parent route):
 *   - Pod or Deployment summary (kind-dependent shape).
 *   - The incident list filtered to this workload.
 *   - Active-incidents list for the open-incidents chip.
 *
 * Animates in with a single fade-up; the body uses the brand-color
 * border-left to call out unhealthy state without needing red text.
 */

type WorkloadKind = "Pod" | "Deployment" | "Node";

interface Props {
  kind: WorkloadKind;
  namespace: string;
  name: string;
  pod?: PodSummary | null;
  deployment?: DeploymentSummary | null;
  incidents: IncidentSummary[];
  activeCount: number;
}

export function WorkloadHeaderCard({
  kind,
  namespace,
  name,
  pod,
  deployment,
  incidents,
  activeCount,
}: Props) {
  const { t } = useTranslation();

  // ---- Health derivation ---------------------------------------------
  // We're explicit per-kind so the chip color + label match what's
  // actually being measured. The same "unhealthy" rail Komodor uses
  // on the workload page is driven from these here.
  let healthLabel = "—";
  let healthVariant: "ok" | "warn" | "danger" | "secondary" = "secondary";
  let replicasLabel: string | null = null;
  let image: string | null = null;
  let ageSeconds: number | null = null;
  const node: string | null = null;
  let restarts: number | null = null;

  if (kind === "Pod" && pod) {
    healthLabel = pod.phase;
    healthVariant =
      pod.phase === "Running" ? "ok" : pod.phase === "Pending" ? "warn" : "danger";
    // PodSummary doesn't expose image / node in the agent's projection;
    // those are only on `kubectl describe pod` which we don't fan out
    // for the header. The PodDetailDrawer still surfaces them on click.
    ageSeconds = pod.age_seconds;
    restarts = pod.restarts;
  } else if (kind === "Deployment" && deployment) {
    const ready = deployment.replicas_ready ?? 0;
    const desired = deployment.replicas_desired ?? 0;
    healthLabel = deployment.available ? "Available" : "Degraded";
    healthVariant =
      deployment.available && ready === desired
        ? "ok"
        : ready === 0
          ? "danger"
          : "warn";
    replicasLabel = `${ready}/${desired}`;
    image = deployment.images?.[0] ?? null;
    ageSeconds = deployment.age_seconds;
  }

  // ---- Last action derivation ----------------------------------------
  // Walk the incident history backwards (the list is newest-first) and
  // find the first one whose `proposed_tool` is set — that's the most
  // recent thing the agent did to this workload.
  const lastIncidentWithAction = incidents.find((i) => i.proposed_tool);
  const lastAction = lastIncidentWithAction?.proposed_tool ?? null;
  const lastActionOutcome = lastIncidentWithAction
    ? lastIncidentWithAction.healed === true
      ? "healed"
      : lastIncidentWithAction.escalated
        ? "escalated"
        : lastIncidentWithAction.healed === false
          ? "failed"
          : "—"
    : null;
  const lastActionVariant =
    lastActionOutcome === "healed"
      ? "ok"
      : lastActionOutcome === "escalated"
        ? "warn"
        : lastActionOutcome === "failed"
          ? "danger"
          : "secondary";

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <Card className={cn("border-l-4", healthRailClass(healthVariant))}>
        <CardContent className="p-4">
          {/* Identity row: kind badge + namespace/name */}
          <div className="flex flex-wrap items-baseline gap-3">
            <Badge variant="secondary" className="text-[10px] uppercase">
              {kind}
            </Badge>
            <h1 className="truncate font-mono text-lg font-semibold">{name}</h1>
            <span className="text-sm text-muted-foreground">
              <span className="opacity-60">ns:</span>{" "}
              <span className="font-mono">{namespace}</span>
            </span>
            {activeCount > 0 ? (
              <Badge variant="warn" className="gap-1 uppercase">
                <span className="h-2 w-2 animate-node-pulse bg-warn" aria-hidden />
                {activeCount} {t("workload.header.activeIncidents", { defaultValue: "active" })}
              </Badge>
            ) : null}
          </div>

          {/* Stat chips row */}
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Chip
              icon={<AlertTriangle className="h-4 w-4" />}
              label={t("workload.header.health", { defaultValue: "Health" })}
              value={healthLabel}
              variant={healthVariant}
            />
            {replicasLabel ? (
              <Chip
                icon={<Box className="h-4 w-4" />}
                label={t("workload.header.replicas", { defaultValue: "Replicas" })}
                value={replicasLabel}
                variant={
                  replicasLabel.split("/")[0] === replicasLabel.split("/")[1]
                    ? "ok"
                    : "warn"
                }
              />
            ) : null}
            {ageSeconds != null ? (
              <Chip
                icon={<History className="h-4 w-4" />}
                label={t("workload.header.age", { defaultValue: "Age" })}
                value={relativeTimeSeconds(ageSeconds)}
                variant="secondary"
              />
            ) : null}
            {restarts != null && restarts > 0 ? (
              <Chip
                icon={<Cpu className="h-4 w-4" />}
                label={t("workload.header.restarts", { defaultValue: "Restarts" })}
                value={String(restarts)}
                variant="warn"
              />
            ) : null}
            {node ? (
              <Chip
                icon={<Server className="h-4 w-4" />}
                label={t("workload.header.node", { defaultValue: "Node" })}
                value={node}
                variant="secondary"
              />
            ) : null}
            {lastAction ? (
              <Chip
                icon={<GitBranch className="h-4 w-4" />}
                label={t("workload.header.lastAction", { defaultValue: "Last action" })}
                value={lastAction}
                hint={lastActionOutcome ?? undefined}
                variant={lastActionVariant}
              />
            ) : null}
            {image ? (
              <Chip
                icon={<ImageIcon className="h-4 w-4" />}
                label={t("workload.header.image", { defaultValue: "Image" })}
                value={image.split("/").slice(-1)[0]}
                hint={image.split(":")[1] ?? null}
                variant="secondary"
              />
            ) : null}
          </div>

          {/* Open-incidents summary row */}
          {incidents.length > 0 ? (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="uppercase tracking-wider">
                {t("workload.header.history", { defaultValue: "History" })}:
              </span>
              <span>
                {incidents.length}{" "}
                {t("workload.header.incidentsCount", { defaultValue: "incidents" })}
              </span>
              <span className="opacity-50">·</span>
              <span>
                {incidents.filter((i) => i.healed === true).length}{" "}
                <span className="text-ok">●</span>{" "}
                {t("workload.header.healed", { defaultValue: "healed" })}
              </span>
              <span>
                {incidents.filter((i) => i.escalated).length}{" "}
                <span className="text-warn">●</span>{" "}
                {t("workload.header.escalated", { defaultValue: "escalated" })}
              </span>
              <span>
                {incidents.filter((i) => i.healed === false).length}{" "}
                <span className="text-danger">●</span>{" "}
                {t("workload.header.failed", { defaultValue: "failed" })}
              </span>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </motion.div>
  );
}

function healthRailClass(v: "ok" | "warn" | "danger" | "secondary"): string {
  switch (v) {
    case "ok":
      return "border-l-ok";
    case "warn":
      return "border-l-warn";
    case "danger":
      return "border-l-danger";
    default:
      return "border-l-border";
  }
}

interface ChipProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string | null;
  variant?: "ok" | "warn" | "danger" | "info" | "secondary";
}

function Chip({ icon, label, value, hint, variant = "secondary" }: ChipProps) {
  const iconColor: Record<NonNullable<ChipProps["variant"]>, string> = {
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
    info: "text-info",
    secondary: "text-muted-foreground",
  };
  return (
    <div className="flex items-start gap-2">
      <span className={cn("mt-0.5 shrink-0", iconColor[variant])}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
        <div className="truncate text-sm font-semibold leading-tight">{value}</div>
        {hint ? <div className="truncate text-[10px] text-muted-foreground">{hint}</div> : null}
      </div>
    </div>
  );
}
