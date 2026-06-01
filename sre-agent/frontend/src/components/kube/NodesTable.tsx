import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Cpu } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";

export function NodesTable() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["nodes"],
    queryFn: api.listNodes,
    refetchInterval: 8000,
  });

  if (q.isLoading) {
    return <p className="text-sm text-muted-foreground">{t("common.loading")}</p>;
  }
  if ((q.data ?? []).length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("cluster.nodes.empty")}
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
            <TableHead>{t("cluster.nodes.columns.name")}</TableHead>
            <TableHead>{t("cluster.nodes.columns.status")}</TableHead>
            <TableHead>{t("cluster.nodes.columns.version")}</TableHead>
            <TableHead>{t("cluster.nodes.columns.taints")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(q.data ?? []).map((n) => (
            <TableRow key={n.name}>
              <TableCell>
                <Cpu className="h-4 w-4 text-muted-foreground" />
              </TableCell>
              <TableCell className="font-mono text-xs">{n.name}</TableCell>
              <TableCell>
                {n.ready ? (
                  <Badge variant="ok">{t("cluster.nodes.ready")}</Badge>
                ) : (
                  <Badge variant="danger">{t("cluster.nodes.notReady")}</Badge>
                )}
              </TableCell>
              <TableCell className="font-mono text-xs">{n.version}</TableCell>
              <TableCell className="font-mono text-xs">
                {n.taints.length ? n.taints.join(", ") : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
