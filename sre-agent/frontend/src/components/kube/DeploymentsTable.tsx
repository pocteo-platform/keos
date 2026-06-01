import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { relativeTimeSeconds } from "@/lib/utils";

export function DeploymentsTable({ ns }: { ns: string }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["deployments", ns],
    queryFn: () => api.listDeployments(ns),
    refetchInterval: 5000,
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">{t("common.loading")}</p>;
  }
  if ((q.data ?? []).length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("cluster.deployments.empty")}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8"></TableHead>
            <TableHead>{t("cluster.deployments.columns.name")}</TableHead>
            <TableHead>{t("cluster.deployments.columns.replicas")}</TableHead>
            <TableHead>{t("cluster.deployments.columns.images")}</TableHead>
            <TableHead>{t("cluster.deployments.columns.age")}</TableHead>
            <TableHead>{t("cluster.deployments.columns.status")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(q.data ?? []).map((d) => (
            <TableRow key={d.name}>
              <TableCell>
                <Boxes className="h-4 w-4 text-muted-foreground" />
              </TableCell>
              <TableCell className="font-mono text-xs">{d.name}</TableCell>
              <TableCell className="font-mono text-xs">
                {d.replicas_ready}/{d.replicas_desired}
                {d.replicas_updated !== d.replicas_desired ? (
                  <span className="text-muted-foreground">
                    {" "}
                    ({d.replicas_updated} {t("cluster.deployments.updated")})
                  </span>
                ) : null}
              </TableCell>
              <TableCell className="max-w-md truncate font-mono text-xs">
                {d.images.join(", ")}
              </TableCell>
              <TableCell className="font-mono text-xs">
                {relativeTimeSeconds(d.age_seconds)}
              </TableCell>
              <TableCell>
                {d.available ? (
                  <Badge variant="ok">{t("cluster.deployments.available")}</Badge>
                ) : (
                  <Badge variant="warn">{t("cluster.deployments.degraded")}</Badge>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
