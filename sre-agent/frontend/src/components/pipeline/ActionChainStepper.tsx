import { useTranslation } from "react-i18next";
import { motion } from "motion/react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SceneStep } from "@/lib/api";

/**
 * Vertical stepper that walks every graph-node entry the agent
 * went through for this incident. Mirrors the diagnose → plan →
 * gate → execute → verify chain Komodor's "events table" implies
 * but adapts to OUR loop instead of generic K8s events.
 *
 * Each step's status drives the left rail color:
 *   ok    — green
 *   warn  — amber  (low confidence, OPA deny, verify=False)
 *   error — red    (subprocess failure, unhandled exception)
 *   skipped — muted (graph short-circuited at this step)
 */

const STATUS_VARIANT: Record<SceneStep["status"], "ok" | "warn" | "danger" | "secondary"> = {
  ok: "ok",
  warn: "warn",
  error: "danger",
  skipped: "secondary",
};

interface Props {
  steps: SceneStep[];
}

export function ActionChainStepper({ steps }: Props) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wider">
          {t("scene.chain.title", { defaultValue: "Action chain" })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {steps.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("scene.chain.empty", {
              defaultValue: "Graph hasn't started yet.",
            })}
          </p>
        ) : (
          <ol className="relative space-y-3 border-l border-border pl-5">
            {steps.map((s, i) => (
              <motion.li
                key={`${s.name}-${i}`}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.18, delay: i * 0.05 }}
                className="relative"
              >
                {/* node dot on the rail */}
                <span
                  className={cn(
                    "absolute -left-[1.55rem] top-1.5 h-3 w-3",
                    s.status === "ok" && "bg-ok",
                    s.status === "warn" && "bg-warn",
                    s.status === "error" && "bg-danger",
                    s.status === "skipped" && "bg-muted-foreground/50",
                  )}
                  aria-hidden
                />
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-xs uppercase tracking-wider">{s.name}</span>
                  <Badge variant={STATUS_VARIANT[s.status]} className="text-[10px]">
                    {t(`scene.chain.status.${s.status}`, {
                      defaultValue: s.status,
                    })}
                  </Badge>
                </div>
                <p className="mt-0.5 text-xs text-foreground/90">{s.summary}</p>
              </motion.li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
