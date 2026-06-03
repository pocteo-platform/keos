import { useTranslation } from "react-i18next";
import { motion } from "motion/react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Komodor-style "Workload Health" / "Infrastructure Health" section.
 * Two halves:
 *   - Realtime Issues: count tiles per issue kind
 *   - Reliability Risks: severity-bucketed counts (H / M / L)
 *
 * Both visualised the same way: a row of tiles with an integer
 * count + glyph + label. The parent route composes the data and
 * passes it in — no API calls live here. Keeps the page snappy
 * and testable.
 */

export interface IssueTile {
  /** i18n key tail; the component renders t(`reliability.tiles.<key>`). */
  key: string;
  /** Display value (count or string). */
  value: string | number;
  /** Override fallback if i18n key is missing. */
  defaultLabel?: string;
  /** Lucide icon component. */
  icon: React.ReactNode;
  /** Visual variant — drives the left rail color. */
  variant?: "ok" | "warn" | "danger" | "info" | "default";
  /** Optional sub-line (e.g. severity breakdown). */
  hint?: string;
}

export interface RiskBucket {
  /** i18n key tail; the component renders t(`reliability.risks.<key>`). */
  key: string;
  defaultLabel?: string;
  /** High / Medium / Low counts. */
  high: number;
  medium: number;
  low: number;
}

interface Props {
  title: string;
  realtime: IssueTile[];
  risks?: RiskBucket[];
}

export function HealthSection({ title, realtime, risks }: Props) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Realtime Issues row */}
        <div>
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("reliability.section.realtime", { defaultValue: "Realtime issues" })}
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {realtime.map((tile, i) => (
              <Tile key={tile.key} tile={tile} index={i} />
            ))}
          </div>
        </div>
        {/* Reliability Risks row */}
        {risks && risks.length > 0 ? (
          <div>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("reliability.section.risks", { defaultValue: "Reliability risks" })}
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {risks.map((b) => (
                <RiskTile key={b.key} bucket={b} />
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Tile({ tile, index }: { tile: IssueTile; index: number }) {
  const { t } = useTranslation();
  const variant = tile.variant ?? "default";
  const rail: Record<string, string> = {
    ok: "border-l-ok bg-ok/5",
    warn: "border-l-warn bg-warn/5",
    danger: "border-l-danger bg-danger/5",
    info: "border-l-info bg-info/5",
    default: "border-l-border",
  };
  const tone: Record<string, string> = {
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
    info: "text-info",
    default: "text-muted-foreground",
  };
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, delay: index * 0.03 }}
      className={cn("border-l-4 p-3", rail[variant])}
    >
      <div className="flex items-start gap-2">
        <span className={cn("mt-0.5 shrink-0", tone[variant])}>{tile.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t(`reliability.tiles.${tile.key}`, {
              defaultValue: tile.defaultLabel ?? tile.key,
            })}
          </div>
          <div className="text-xl font-semibold leading-tight">{tile.value}</div>
          {tile.hint ? (
            <div className="mt-0.5 text-[11px] text-muted-foreground">{tile.hint}</div>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}

function RiskTile({ bucket }: { bucket: RiskBucket }) {
  const { t } = useTranslation();
  return (
    <div className="border border-border p-3">
      <div className="text-xs font-medium">
        {t(`reliability.risks.${bucket.key}`, {
          defaultValue: bucket.defaultLabel ?? bucket.key,
        })}
      </div>
      <div className="mt-1.5 flex items-center gap-3 text-xs">
        <SevBadge label="H" count={bucket.high} variant="danger" />
        <SevBadge label="M" count={bucket.medium} variant="warn" />
        <SevBadge label="L" count={bucket.low} variant="info" />
      </div>
    </div>
  );
}

function SevBadge({
  label,
  count,
  variant,
}: {
  label: string;
  count: number;
  variant: "danger" | "warn" | "info";
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge variant={variant} className="h-5 w-5 justify-center p-0 text-[10px]">
        {label}
      </Badge>
      <span className="font-mono">{count}</span>
    </span>
  );
}
