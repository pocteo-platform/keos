import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Activity, Radio } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, type ActiveIncident } from "@/lib/api";

interface Props {
  /** Currently-following incident_id (null = "auto: follow newest"). */
  current: string | null;
  /** All incidents seen on the WS so far this session, oldest-first. */
  seen: ActiveIncident[];
  /** Set null → auto-follow; set string → lock to that incident. */
  onChange: (incidentId: string | null) => void;
}

/** Lets the operator choose which in-flight incident the canvas tracks.
 *  Combines two data sources:
 *    1. Server-known active incidents (`/api/incidents/active`) — recovers
 *       choices across a page reload.
 *    2. Locally-seen IDs from the WS stream this session — surfaces any
 *       incident that fired *while* the user was watching, even if the
 *       backend hasn't logged it yet. */
export function IncidentPicker({ current, seen, onChange }: Props) {
  const { t } = useTranslation();
  const activeQ = useQuery({
    queryKey: ["incidents", "active"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 5000,
  });

  // Merge server-side + locally-seen; dedupe by incident_id; newest first.
  const merged = useMemo(() => {
    const byId = new Map<string, ActiveIncident>();
    for (const i of activeQ.data ?? []) byId.set(i.incident_id, i);
    for (const i of seen) {
      if (!byId.has(i.incident_id)) byId.set(i.incident_id, i);
    }
    return [...byId.values()].sort((a, b) =>
      (b.started_at ?? "").localeCompare(a.started_at ?? ""),
    );
  }, [activeQ.data, seen]);

  const followsAuto = current === null;
  const choice = current ?? "__auto__";

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 py-3">
        <div className="flex items-center gap-2 text-sm">
          <Radio className="h-4 w-4 text-info" />
          <span className="font-medium">{t("pipeline.live.picker.label")}</span>
        </div>

        <ToggleGroup
          type="single"
          value={followsAuto ? "auto" : "manual"}
          onValueChange={(v) => {
            if (!v) return;
            if (v === "auto") onChange(null);
            else if (merged[0]) onChange(merged[0].incident_id);
          }}
        >
          <ToggleGroupItem value="auto" className="text-xs">
            {t("pipeline.live.picker.auto")}
          </ToggleGroupItem>
          <ToggleGroupItem value="manual" className="text-xs" disabled={merged.length === 0}>
            {t("pipeline.live.picker.manual")}
          </ToggleGroupItem>
        </ToggleGroup>

        <div className="flex flex-1 items-center gap-2 min-w-[20rem]">
          <Label htmlFor="incident-picker" className="text-xs text-muted-foreground">
            {t("pipeline.live.picker.choose")}
          </Label>
          <Select value={choice} onValueChange={(v) => onChange(v === "__auto__" ? null : v)}>
            <SelectTrigger id="incident-picker" className="flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__auto__">
                <span className="flex items-center gap-2">
                  <Activity className="h-3 w-3" />
                  {t("pipeline.live.picker.autoOption")}
                </span>
              </SelectItem>
              {merged.length === 0 ? (
                <SelectItem value="__empty__" disabled>
                  {t("pipeline.live.picker.noneActive")}
                </SelectItem>
              ) : (
                merged.map((i) => (
                  <SelectItem key={i.incident_id} value={i.incident_id}>
                    <span className="flex items-center gap-2 font-mono text-xs">
                      {i.incident_id}
                      {i.severity ? (
                        <Badge variant="secondary" className="text-[10px]">
                          {i.severity}
                        </Badge>
                      ) : null}
                      <span className="text-muted-foreground">
                        {i.namespace}/{i.pod}
                      </span>
                    </span>
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>
            {merged.length} {t("pipeline.live.picker.activeCount")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => activeQ.refetch()}
            disabled={activeQ.isFetching}
          >
            {t("common.retry")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
