import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RotateCw } from "lucide-react";
import { cn, relativeTimeSeconds } from "@/lib/utils";
import type { PodSummary } from "@/lib/api";

const phaseVariant = (phase: string): "ok" | "warn" | "danger" => {
  if (phase === "Running" || phase === "Succeeded") return "ok";
  if (phase === "Pending") return "warn";
  return "danger";
};

interface Props {
  pod: PodSummary;
  recentlyChanged?: boolean;
  onClick?: (pod: PodSummary) => void;
}

export function PodCard({ pod, recentlyChanged, onClick }: Props) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={() => onClick?.(pod)}
      className={cn("text-left", "transition-shadow", recentlyChanged && "ring-2 ring-info")}
    >
      <Card className="hover:bg-accent">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center justify-between gap-2 text-base">
            <span className="truncate font-mono text-sm">{pod.name}</span>
            <Badge variant={phaseVariant(pod.phase)}>{pod.phase}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 pb-4 text-xs">
          <div>
            <div className="text-muted-foreground">{t("cluster.podCard.ready")}</div>
            <div className="font-mono">{pod.ready}</div>
          </div>
          <div>
            <div className="text-muted-foreground">{t("cluster.podCard.restarts")}</div>
            <div className="flex items-center gap-1 font-mono">
              {pod.restarts > 0 ? <RotateCw className="h-3 w-3 text-warn" /> : null}
              {pod.restarts}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground">{t("cluster.podCard.age")}</div>
            <div className="font-mono">{relativeTimeSeconds(pod.age_seconds)}</div>
          </div>
          <div>
            <div className="text-muted-foreground">{t("cluster.podCard.owner")}</div>
            <div className="truncate">
              {pod.owner_kind ? (
                <span>
                  {pod.owner_kind}/{pod.owner_name}
                </span>
              ) : (
                <span className="text-muted-foreground">{t("cluster.podCard.noOwner")}</span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </button>
  );
}
