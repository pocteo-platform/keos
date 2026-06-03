import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { format } from "date-fns";
import { enUS, fr as frLocale } from "date-fns/locale";
import { ArrowUpDown, ChevronDown, ChevronUp } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { HmacChip } from "./HmacChip";
import { useApp } from "@/lib/store";
import type { AuditRecord } from "@/lib/api";

const PAGE = 50;

type SortKey = "ts" | "event_type" | "incident_id";
type SortDir = "asc" | "desc";

/**
 * Phase 14.7 — full pagination + sortable columns on the audit table.
 * Previously the table dumped everything in reverse order with no
 * paging or sort affordances; with 200+ records per scenario run this
 * was unusable for forensic work.
 */
export function AuditTable({ records }: { records: AuditRecord[] }) {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const dfLocale = locale === "fr" ? frLocale : enUS;
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<AuditRecord | null>(null);
  const [offset, setOffset] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("ts");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return records;
    return records.filter(
      (r) =>
        r.event_type.toLowerCase().includes(needle) ||
        r.incident_id.toLowerCase().includes(needle) ||
        JSON.stringify(r.payload).toLowerCase().includes(needle),
    );
  }, [records, filter]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const va = a[sortKey] as string;
      const vb = b[sortKey] as string;
      const cmp = va.localeCompare(vb);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const total = sorted.length;
  const page = sorted.slice(offset, offset + PAGE);
  const pageNum = Math.floor(offset / PAGE);
  const pageMax = Math.max(0, Math.ceil(total / PAGE) - 1);

  function toggleSort(k: SortKey) {
    if (sortKey === k) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir("desc");
    }
    setOffset(0);
  }

  function SortHeader({ k, label }: { k: SortKey; label: string }) {
    const active = sortKey === k;
    return (
      <button
        type="button"
        onClick={() => toggleSort(k)}
        className="inline-flex items-center gap-1 hover:text-foreground"
      >
        {label}
        {active ? (
          sortDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-50" />
        )}
      </button>
    );
  }

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <Input
          placeholder={t("common.search")}
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setOffset(0);
          }}
          className="max-w-sm"
        />
        <span className="text-xs text-muted-foreground">
          {t("audit.matching", {
            count: total,
            defaultValue_one: "{{count}} record",
            defaultValue_other: "{{count}} records",
          })}
        </span>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <SortHeader k="ts" label={t("control.audit.columns.ts")} />
              </TableHead>
              <TableHead>
                <SortHeader k="event_type" label={t("control.audit.columns.event")} />
              </TableHead>
              <TableHead>
                <SortHeader k="incident_id" label={t("control.audit.columns.incident")} />
              </TableHead>
              <TableHead>{t("control.audit.columns.source")}</TableHead>
              <TableHead>{t("control.audit.columns.hmac")}</TableHead>
              <TableHead>{t("control.audit.columns.preview")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                  {t("common.empty")}
                </TableCell>
              </TableRow>
            ) : (
              page.map((r) => (
                <TableRow
                  key={`${r.incident_id}-${r.ts}-${r.event_type}-${r.hmac.slice(0, 8)}`}
                  onClick={() => setSelected(r)}
                  className="cursor-pointer"
                >
                  <TableCell className="whitespace-nowrap font-mono text-xs">
                    {format(new Date(r.ts), "yyyy-MM-dd HH:mm:ss.SSS", { locale: dfLocale })}
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
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {t("incidents.pagination.showing", {
            from: total === 0 ? 0 : offset + 1,
            to: Math.min(offset + PAGE, total),
            total,
            defaultValue: `Showing ${total === 0 ? 0 : offset + 1}–${Math.min(offset + PAGE, total)} of ${total}`,
          })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={pageNum <= 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            {t("incidents.pagination.prev", { defaultValue: "Prev" })}
          </Button>
          <span className="font-mono text-xs">
            {pageNum + 1}/{pageMax + 1}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={pageNum >= pageMax}
            onClick={() => setOffset(offset + PAGE)}
          >
            {t("incidents.pagination.next", { defaultValue: "Next" })}
          </Button>
        </div>
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
