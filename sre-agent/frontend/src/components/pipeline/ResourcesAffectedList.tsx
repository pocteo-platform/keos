import { useTranslation } from "react-i18next";
import { motion } from "motion/react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SceneResource } from "@/lib/api";

/**
 * The "resources affected" pane in the use-case-launch scene view.
 * Renders one row per K8s object the agent touched during this
 * incident, color-coded by the outcome bucket:
 *
 *   reconciled — green   (action ran + verify=healed)
 *   paged      — amber   (agent escalated; no mutation done)
 *   failed     — red     (action ran but verify=False)
 *   unchanged  — neutral (observed, not acted on — e.g. the chaos CR)
 *
 * Rows animate in stagger when the scene first renders.
 */

const OUTCOME_VARIANT: Record<SceneResource["outcome"], "ok" | "warn" | "danger" | "secondary"> = {
  reconciled: "ok",
  paged: "warn",
  failed: "danger",
  unchanged: "secondary",
};

const OUTCOME_GLYPH: Record<SceneResource["outcome"], string> = {
  reconciled: "✓",
  paged: "⚠",
  failed: "✕",
  unchanged: "•",
};

interface Props {
  resources: SceneResource[];
}

export function ResourcesAffectedList({ resources }: Props) {
  const { t } = useTranslation();
  // Dedup by (kind, name, namespace) — the projection may surface
  // the same row from multiple audit sources (the original target
  // pod + the action's args target).
  const seen = new Set<string>();
  const deduped = resources.filter((r) => {
    const key = `${r.kind}/${r.namespace ?? ""}/${r.name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wider">
          {t("scene.resources.title", { defaultValue: "Resources affected" })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {deduped.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("scene.resources.empty", {
              defaultValue: "No K8s objects touched yet.",
            })}
          </p>
        ) : (
          <ul className="space-y-2">
            {deduped.map((r, i) => (
              <motion.li
                key={`${r.kind}-${r.namespace ?? "_"}-${r.name}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.18, delay: i * 0.04 }}
                className={cn(
                  "flex items-center gap-3 border-l-2 px-2 py-1.5",
                  r.outcome === "reconciled" && "border-ok",
                  r.outcome === "paged" && "border-warn",
                  r.outcome === "failed" && "border-danger",
                  r.outcome === "unchanged" && "border-muted-foreground/40",
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center font-mono text-sm",
                    r.outcome === "reconciled" && "text-ok",
                    r.outcome === "paged" && "text-warn",
                    r.outcome === "failed" && "text-danger",
                    r.outcome === "unchanged" && "text-muted-foreground",
                  )}
                  aria-hidden
                >
                  {OUTCOME_GLYPH[r.outcome]}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <Badge variant="secondary" className="font-mono uppercase">
                      {r.kind}
                    </Badge>
                    <span className="truncate font-mono text-xs">
                      {r.namespace ? `${r.namespace}/` : ""}
                      {r.name}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">{r.state}</p>
                </div>
                <Badge variant={OUTCOME_VARIANT[r.outcome]}>
                  {t(`scene.resources.outcome.${r.outcome}`, {
                    defaultValue: r.outcome,
                  })}
                </Badge>
              </motion.li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
