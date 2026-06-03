import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNowStrict } from "date-fns";
import { enUS, fr as frLocale } from "date-fns/locale";
import { AlertTriangle, Info } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";

type Filter = "all" | "warn";

const PAGE = 25;

export function EventsList({ ns }: { ns: string }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const locale = useApp((s) => s.locale);
  const dfLocale = locale === "fr" ? frLocale : enUS;

  const q = useQuery({
    queryKey: ["events", ns],
    queryFn: () => api.listEvents(ns, 200),
    refetchInterval: 4000,
  });

  // Reset to page 0 whenever the namespace or filter changes.
  useEffect(() => {
    setOffset(0);
  }, [ns, filter, search]);

  const events = useMemo(
    () =>
      (q.data ?? []).filter((e) => {
        if (filter === "warn" && e.type !== "Warning") return false;
        if (search) {
          const needle = search.toLowerCase();
          const hay = `${e.reason} ${e.message} ${e.involved_name ?? ""}`.toLowerCase();
          if (!hay.includes(needle)) return false;
        }
        return true;
      }),
    [q.data, filter, search],
  );

  const total = events.length;
  const page = events.slice(offset, offset + PAGE);
  const pageNum = Math.floor(offset / PAGE);
  const pageMax = Math.max(0, Math.ceil(total / PAGE) - 1);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="text-xs text-muted-foreground">{t("common.filter")}</label>
          <Select value={filter} onValueChange={(v) => setFilter(v as Filter)}>
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("common.all")}</SelectItem>
              <SelectItem value="warn">{t("cluster.events.warningsOnly")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1 min-w-64">
          <label className="text-xs text-muted-foreground">{t("common.search")}</label>
          <Input
            placeholder={t("cluster.events.searchPlaceholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {q.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("common.loading")}</p>
      ) : events.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            {t("cluster.events.empty")}
          </CardContent>
        </Card>
      ) : (
        <ul className="space-y-2">
          {page.map((e, idx) => {
            const isWarn = e.type === "Warning";
            const ago = e.last_seen
              ? formatDistanceToNowStrict(new Date(e.last_seen), {
                  locale: dfLocale,
                  addSuffix: true,
                })
              : "—";
            return (
              <li
                key={`${e.name}-${idx}`}
                className={`flex items-start gap-3 rounded-md border p-3 ${
                  isWarn ? "border-warn/40 bg-warn/5" : ""
                }`}
              >
                <div className="mt-0.5">
                  {isWarn ? (
                    <AlertTriangle className="h-4 w-4 text-warn" />
                  ) : (
                    <Info className="h-4 w-4 text-info" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Badge variant={isWarn ? "warn" : "secondary"}>{e.reason}</Badge>
                    <span className="font-mono">
                      {e.involved_kind}/{e.involved_name}
                    </span>
                    {e.count > 1 ? (
                      <Badge variant="outline" className="text-[10px]">
                        ×{e.count}
                      </Badge>
                    ) : null}
                    <span className="ml-auto text-muted-foreground">{ago}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{e.message}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {events.length > 0 ? (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {t("incidents.pagination.showing", {
              from: offset + 1,
              to: Math.min(offset + PAGE, total),
              total,
              defaultValue: `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}`,
            })}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7"
              disabled={pageNum <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              {t("incidents.pagination.prev", { defaultValue: "Prev" })}
            </Button>
            <span className="font-mono text-[11px]">
              {pageNum + 1}/{pageMax + 1}
            </span>
            <Button
              size="sm"
              variant="outline"
              className="h-7"
              disabled={pageNum >= pageMax}
              onClick={() => setOffset(offset + PAGE)}
            >
              {t("incidents.pagination.next", { defaultValue: "Next" })}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
