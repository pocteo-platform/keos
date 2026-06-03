import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { LayoutGrid } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api, type NamespaceSummary, type PodSummary, type DeploymentSummary } from "@/lib/api";

/**
 * Namespace × resource-kind problem heatmap. For every namespace
 * the cluster knows about, count:
 *
 *   - unhealthy pods (phase != Running)
 *   - non-Available deployments
 *   - open incidents touching the namespace
 *
 * Cells are color-coded by severity (count > 0 → warn, ≥ 3 → danger).
 * Operator sees at a glance which namespace deserves attention,
 * and each row links into the existing /cluster view filtered to
 * that ns. Komodor's "Workload Health" matrix maps directly to
 * this — but adapted to our per-edge scope (single cluster, so
 * no cluster column).
 */
export function NamespaceProblemMap() {
  const { t } = useTranslation();
  const nsQ = useQuery({
    queryKey: ["namespaces"],
    queryFn: api.listNamespaces,
    refetchInterval: 10_000,
  });
  const incQ = useQuery({
    queryKey: ["incidents", { active: true }],
    queryFn: api.listActiveIncidents,
    refetchInterval: 5_000,
  });
  const namespaces = nsQ.data ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
          <LayoutGrid className="h-4 w-4 text-muted-foreground" />
          {t("dashboard.problemMap.title", {
            defaultValue: "Namespace × resource health",
          })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {namespaces.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("dashboard.problemMap.empty", {
              defaultValue: "No namespaces visible — is `make cluster` up?",
            })}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-2 py-2 font-medium uppercase tracking-wider">
                    {t("dashboard.problemMap.cols.namespace", { defaultValue: "Namespace" })}
                  </th>
                  <th className="px-2 py-2 text-center font-medium uppercase tracking-wider">
                    {t("dashboard.problemMap.cols.pods", { defaultValue: "Pods" })}
                  </th>
                  <th className="px-2 py-2 text-center font-medium uppercase tracking-wider">
                    {t("dashboard.problemMap.cols.deployments", {
                      defaultValue: "Deployments",
                    })}
                  </th>
                  <th className="px-2 py-2 text-center font-medium uppercase tracking-wider">
                    {t("dashboard.problemMap.cols.incidents", {
                      defaultValue: "Active incidents",
                    })}
                  </th>
                </tr>
              </thead>
              <tbody>
                {namespaces.map((ns) => (
                  <NamespaceRow
                    key={ns.name}
                    ns={ns}
                    activeIncidentsForNs={
                      incQ.data?.filter((i) => i.namespace === ns.name).length ?? 0
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface RowProps {
  ns: NamespaceSummary;
  activeIncidentsForNs: number;
}

function NamespaceRow({ ns, activeIncidentsForNs }: RowProps) {
  // Pull pods + deployments for THIS namespace in parallel. Each
  // namespace row issues two small queries; TanStack Query
  // dedupes by key so re-renders don't re-fetch.
  const podsQ = useQuery({
    queryKey: ["pods", ns.name],
    queryFn: () => api.listPods(ns.name),
    refetchInterval: 6_000,
  });
  const deployQ = useQuery({
    queryKey: ["deployments", ns.name],
    queryFn: () => api.listDeployments(ns.name),
    refetchInterval: 10_000,
  });

  const pods: PodSummary[] = podsQ.data ?? [];
  const deploys: DeploymentSummary[] = deployQ.data ?? [];

  const unhealthyPods = useMemo(
    () => pods.filter((p) => p.phase !== "Running" && p.phase !== "Succeeded"),
    [pods],
  );
  const unhealthyDeploys = useMemo(
    // Available reads from `available_replicas/replicas` on the K8s
    // API. The agent's DeploymentSummary projects those to
    // `replicas_ready/replicas_desired`. A deployment is unhealthy
    // when fewer pods are Ready than desired, OR the K8s
    // Available=True condition is false.
    () =>
      deploys.filter(
        (d) => !d.available || d.replicas_ready < d.replicas_desired,
      ),
    [deploys],
  );

  return (
    <tr className="border-b border-border/60 hover:bg-accent/30">
      <td className="px-2 py-2">
        <Link
          to={`/cluster?ns=${encodeURIComponent(ns.name)}`}
          className="font-mono hover:underline"
        >
          {ns.name}
        </Link>
      </td>
      <Cell
        denom={pods.length}
        num={unhealthyPods.length}
        label={fmtBadOverTotal(unhealthyPods.length, pods.length)}
      />
      <Cell
        denom={deploys.length}
        num={unhealthyDeploys.length}
        label={fmtBadOverTotal(unhealthyDeploys.length, deploys.length)}
      />
      <Cell denom={null} num={activeIncidentsForNs} label={String(activeIncidentsForNs)} />
    </tr>
  );
}

interface CellProps {
  denom: number | null;
  num: number;
  label: string;
}

function Cell({ denom, num, label }: CellProps) {
  // Severity bands matched against Komodor's at-a-glance color
  // scheme: 0 problems = green, 1-2 = amber, 3+ = red, no data = muted.
  let variant: "ok" | "warn" | "danger" | "secondary";
  if (denom === 0) variant = "secondary";
  else if (num === 0) variant = "ok";
  else if (num >= 3) variant = "danger";
  else variant = "warn";
  return (
    <td className="px-2 py-2 text-center">
      <Badge
        variant={variant}
        className={cn(
          "min-w-[3.5rem] justify-center font-mono",
          variant === "secondary" && "text-muted-foreground",
        )}
      >
        {label}
      </Badge>
    </td>
  );
}

/** "0/8" when there are issues, "8 ok" when all healthy, "—" when zero
 *  resources of that kind exist in the namespace. Keeps the cell text
 *  short enough that the heatmap stays readable at narrow widths. */
function fmtBadOverTotal(bad: number, total: number) {
  if (total === 0) return "—";
  return bad === 0 ? `${total} ok` : `${bad}/${total}`;
}
