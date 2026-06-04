import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Cpu, HardDrive, Network, Server, ShieldAlert, Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { ClusterContext } from "@/lib/api";

/**
 * Dashboard "Cluster context" card — Phase A of the edge-install design.
 *
 * Shows the operator everything the agent figured out about the cluster
 * it's running inside: distro, K8s version, node roster + roles + GPU,
 * detected operators (Argo CD / Flux / Chaos Mesh / Prometheus /
 * cert-manager / kube-state-metrics), the agent's own placement, and
 * any warnings. Polls every 30 s — cluster context changes infrequently
 * (nodes are added/removed once per upgrade cycle), so a low cadence
 * keeps the API quiet.
 *
 * Komodor + Lens IDE both surface this kind of context-at-a-glance
 * card. The pro-edge twist: we show "is the agent on THIS node?" per
 * row so the operator can see where their SRE actually lives.
 */
export function ClusterContextCard() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["cluster-context"],
    queryFn: api.getClusterContext,
    refetchInterval: 30_000,
  });

  const ctx = q.data;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
            <Server className="h-4 w-4 text-info" />
            {t("dashboard.cluster.title", { defaultValue: "Cluster context" })}
          </CardTitle>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {t("dashboard.cluster.subtitle", {
              defaultValue: "What the agent discovered about its host cluster.",
            })}
          </p>
        </div>
        {ctx ? (
          <Badge variant="secondary" className="font-mono text-[10px] uppercase">
            {ctx.distro}
          </Badge>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-3">
        {q.isLoading || !ctx ? (
          <p className="text-xs text-muted-foreground">
            {t("common.loading", { defaultValue: "Loading…" })}
          </p>
        ) : (
          <>
            {/* ── Header strip: distro + K8s version + total resources ── */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Cell
                icon={<Cpu className="h-3.5 w-3.5" />}
                label={t("dashboard.cluster.k8sVersion", { defaultValue: "Kubernetes" })}
                value={ctx.kubernetes_version}
              />
              <Cell
                icon={<HardDrive className="h-3.5 w-3.5" />}
                label={t("dashboard.cluster.runtime", { defaultValue: "Runtime" })}
                value={ctx.runtime}
              />
              <Cell
                icon={<Network className="h-3.5 w-3.5" />}
                label={t("dashboard.cluster.nodes", { defaultValue: "Nodes" })}
                value={`${ctx.ready_node_count}/${ctx.node_count}`}
                hint={t("dashboard.cluster.nodesHint", { defaultValue: "ready" })}
              />
              <Cell
                icon={<Cpu className="h-3.5 w-3.5" />}
                label={t("dashboard.cluster.capacity", { defaultValue: "Capacity" })}
                value={`${(ctx.total_cpu_m / 1000).toFixed(0)} CPU · ${fmtBytes(ctx.total_memory_bytes)}`}
                hint={
                  ctx.total_gpu_count > 0
                    ? t("dashboard.cluster.gpuHint", {
                        defaultValue: `${ctx.total_gpu_count} GPU`,
                        count: ctx.total_gpu_count,
                      })
                    : undefined
                }
              />
            </div>

            {/* ── Node roster ── */}
            <div className="border border-border bg-muted/20">
              <div className="border-b border-border bg-background/40 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t("dashboard.cluster.nodeRoster", { defaultValue: "Nodes" })}
              </div>
              <ul className="divide-y divide-border/60">
                {ctx.nodes.map((n) => (
                  <li
                    key={n.name}
                    className="flex items-center gap-2 px-3 py-1.5 text-[11px]"
                  >
                    <span
                      className={cn(
                        "inline-block h-1.5 w-1.5 rounded-full",
                        n.ready ? "bg-ok" : "bg-danger",
                      )}
                      aria-label={n.ready ? "ready" : "not ready"}
                    />
                    <span className="font-mono">{n.name}</span>
                    <Badge variant="secondary" className="text-[9px] uppercase">
                      {n.role === "control-plane" ? "ctrl" : n.role}
                    </Badge>
                    {n.gpu_capacity ? (
                      <Badge variant="info" className="gap-1 text-[9px] uppercase">
                        <Sparkles className="h-2.5 w-2.5" />
                        {n.gpu_capacity} GPU
                      </Badge>
                    ) : null}
                    {n.is_agent_home ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge variant="ok" className="gap-1 text-[9px] uppercase">
                            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-ok-foreground/80" />
                            {t("dashboard.cluster.agentHome", {
                              defaultValue: "agent here",
                            })}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs">
                          {t("dashboard.cluster.agentHomeTooltip", {
                            defaultValue:
                              "The Keos agent pod is currently scheduled on this node (downward API).",
                          })}
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                    <span className="ml-auto text-muted-foreground">
                      {n.cpu_capacity_m
                        ? `${(n.cpu_capacity_m / 1000).toFixed(0)} CPU`
                        : "—"}
                      {n.memory_capacity_bytes ? ` · ${fmtBytes(n.memory_capacity_bytes)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            {/* ── Detected operators ── */}
            <div>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t("dashboard.cluster.detectedOps", { defaultValue: "Detected operators" })}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(Object.entries(ctx.detected_operators) as [keyof ClusterContext["detected_operators"], boolean][]).map(([k, v]) => (
                  <Badge
                    key={k}
                    variant={v ? "info" : "secondary"}
                    className={cn(
                      "text-[10px] uppercase tracking-wider",
                      v ? "" : "opacity-50",
                    )}
                  >
                    {k.replace(/_/g, "-")}
                    {v ? " ✓" : " ✗"}
                  </Badge>
                ))}
              </div>
            </div>

            {/* ── Warnings ── */}
            {ctx.warnings.length > 0 ? (
              <div className="space-y-1">
                {ctx.warnings.map((w, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-2 border-l-2 border-warn/50 bg-warn/5 px-2 py-1.5 text-[11px] leading-snug text-foreground/80"
                  >
                    <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0 text-warn" />
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Cell({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="border border-border bg-muted/20 px-2.5 py-1.5">
      <div className="flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        <span>{icon}</span>
        <span>{label}</span>
      </div>
      <div className="mt-0.5 truncate font-mono text-[12px] font-semibold">{value}</div>
      {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

function fmtBytes(n: number): string {
  if (n <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}
