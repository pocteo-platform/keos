import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { format } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { JsonBlock } from "@/components/ui/json-block";
import { HmacChip } from "@/components/audit/HmacChip";
import { api, type AuditRecord } from "@/lib/api";

interface Props {
  incidentId: string;
}

/** Filters the audit log to records for one incident, recomputes the chain
 *  verdict, and offers a per-row "Verify HMAC" detail dialog. */
export function HmacChainVerifier({ incidentId }: Props) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<AuditRecord | null>(null);

  // 500 is enough for any single incident's chain (worst case ~50 records).
  const q = useQuery({
    queryKey: ["audit", "for-incident", incidentId],
    queryFn: () => api.listAudit({ incident_id: incidentId, limit: 500 }),
  });
  const verifyQ = useQuery({
    queryKey: ["audit", "verify"],
    queryFn: api.verifyAudit,
    enabled: Boolean(q.data?.items.length),
  });

  const items = useMemo(
    () => (q.data?.items ?? []).slice().sort((a, b) => a.ts.localeCompare(b.ts)),
    [q.data?.items],
  );

  const allValid = items.length > 0 && items.every((r) => r.hmac_valid);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm">{t("incidents.tabs.audit.title")}</CardTitle>
        <div className="flex items-center gap-3">
          {items.length === 0 ? null : allValid ? (
            <Badge variant="ok">{t("incidents.tabs.audit.chainValid")}</Badge>
          ) : (
            <Badge variant="danger">{t("incidents.tabs.audit.chainBroken")}</Badge>
          )}
          {verifyQ.data ? (
            <span className="text-xs text-muted-foreground">
              {verifyQ.data.valid
                ? t("incidents.tabs.audit.fileValid")
                : t("incidents.tabs.audit.fileBroken")}
            </span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("incidents.tabs.audit.empty")}</p>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("control.audit.columns.ts")}</TableHead>
                  <TableHead>{t("control.audit.columns.event")}</TableHead>
                  <TableHead>{t("control.audit.columns.source")}</TableHead>
                  <TableHead>{t("control.audit.columns.hmac")}</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((r, idx) => (
                  <TableRow
                    key={`${r.ts}-${r.event_type}-${idx}`}
                    onClick={() => setSelected(r)}
                    className="cursor-pointer"
                  >
                    <TableCell className="font-mono text-xs">
                      {format(new Date(r.ts), "HH:mm:ss.SSS")}
                    </TableCell>
                    <TableCell className="font-medium">{r.event_type}</TableCell>
                    <TableCell>
                      {(r.payload as { source?: string }).source === "mcp" ? (
                        <Badge variant="info">mcp</Badge>
                      ) : (
                        <Badge variant="secondary">agent</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <HmacChip valid={r.hmac_valid} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost">
                        {t("common.details")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      <Dialog open={Boolean(selected)} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{selected?.event_type}</DialogTitle>
          </DialogHeader>
          {selected ? (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <span className="text-muted-foreground">incident</span>
                  <div className="font-mono">{selected.incident_id}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">timestamp</span>
                  <div className="font-mono">{selected.ts}</div>
                </div>
              </div>
              <div>
                <HmacChip valid={selected.hmac_valid} />
                <code className="ml-2 break-all text-[10px] text-muted-foreground">
                  {selected.hmac}
                </code>
              </div>
              <JsonBlock data={selected.payload} maxHeightRem={24} />
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
