import { useState } from "react";
import { useTranslation } from "react-i18next";
import { format } from "date-fns";
import { enUS, fr as frLocale } from "date-fns/locale";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { HmacChip } from "./HmacChip";
import { useApp } from "@/lib/store";
import type { AuditRecord } from "@/lib/api";

export function AuditTable({ records }: { records: AuditRecord[] }) {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const dfLocale = locale === "fr" ? frLocale : enUS;
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<AuditRecord | null>(null);

  const filtered = filter
    ? records.filter(
        (r) =>
          r.event_type.toLowerCase().includes(filter.toLowerCase()) ||
          r.incident_id.toLowerCase().includes(filter.toLowerCase()),
      )
    : records;

  return (
    <>
      <div className="mb-3">
        <Input
          placeholder={t("common.search")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="max-w-sm"
        />
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("control.audit.columns.ts")}</TableHead>
              <TableHead>{t("control.audit.columns.event")}</TableHead>
              <TableHead>{t("control.audit.columns.incident")}</TableHead>
              <TableHead>{t("control.audit.columns.source")}</TableHead>
              <TableHead>{t("control.audit.columns.hmac")}</TableHead>
              <TableHead>{t("control.audit.columns.preview")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered
              .slice()
              .reverse()
              .map((r) => (
                <TableRow
                  key={`${r.incident_id}-${r.ts}-${r.event_type}`}
                  onClick={() => setSelected(r)}
                  className="cursor-pointer"
                >
                  <TableCell className="whitespace-nowrap font-mono text-xs">
                    {format(new Date(r.ts), "HH:mm:ss.SSS", { locale: dfLocale })}
                  </TableCell>
                  <TableCell className="font-medium">{r.event_type}</TableCell>
                  <TableCell className="font-mono text-xs">{r.incident_id}</TableCell>
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
                  <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                    {previewPayload(r)}
                  </TableCell>
                </TableRow>
              ))}
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  {t("common.empty")}
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>

      <Dialog open={Boolean(selected)} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{selected?.event_type}</DialogTitle>
          </DialogHeader>
          {selected ? (
            <div className="space-y-4 text-sm">
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
                  {selected.hmac.slice(0, 32)}…
                </code>
              </div>
              <pre className="max-h-96 overflow-auto rounded-md border bg-muted/30 p-3 text-xs">
                {JSON.stringify(selected.payload, null, 2)}
              </pre>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function previewPayload(r: AuditRecord): string {
  const p = r.payload as Record<string, unknown>;
  const tool = p.tool ? `tool=${p.tool}` : null;
  const allow = "allow" in p ? `allow=${p.allow}` : null;
  const summary = (p.summary as string | undefined)?.slice(0, 80);
  const reason = (p.reason as string | undefined)?.slice(0, 80);
  return [tool, allow, summary, reason].filter(Boolean).join(" · ");
}
