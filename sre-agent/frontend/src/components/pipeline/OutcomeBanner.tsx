import { useTranslation } from "react-i18next";
import { motion, AnimatePresence } from "motion/react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SceneOutcome } from "@/lib/api";

/**
 * Komodor-inspired big visual answer: "did the agent heal it or
 * page a human?". Sits at the top of the use-case-launch right-hand
 * pane and animates in once the incident terminates.
 *
 * Five visual variants:
 *   - healed       — green, ✓, time-to-heal pill
 *   - escalated    — amber, ⚠, reason text
 *   - failed       — red, ✕, "did not recover"
 *   - observe_only — info, ⊘, "mode = observe-only"
 *   - unknown      — neutral skeleton (renders while the graph runs)
 *
 * The component is fully driven by the SceneOutcome shape returned
 * by `GET /api/scene/:id` — no extra business logic here, so any
 * future verdict added on the backend lands here without a UI
 * round-trip beyond a TS literal extension.
 */

type Variant = "ok" | "warn" | "danger" | "info" | "default";

const VERDICT_VARIANT: Record<SceneOutcome["verdict"], Variant> = {
  healed: "ok",
  escalated: "warn",
  failed: "danger",
  observe_only: "info",
  unknown: "default",
};

const VERDICT_GLYPH: Record<SceneOutcome["verdict"], string> = {
  healed: "✓",
  escalated: "⚠",
  failed: "✕",
  observe_only: "⊘",
  unknown: "…",
};

// Human-readable fallback shown when i18n hasn't loaded the EN strings
// (also what the vitest stub returns via `t(key, { defaultValue })`).
// Kept in lockstep with the `scene.outcome.<verdict>.title` keys.
const VERDICT_LABEL: Record<SceneOutcome["verdict"], string> = {
  healed: "Healed",
  escalated: "Escalated",
  failed: "Failed",
  observe_only: "Observe only",
  unknown: "Running…",
};

interface Props {
  outcome: SceneOutcome | null;
  /** When true, suppress the "still running" skeleton — useful when
   *  the pipeline page knows the graph hasn't terminated yet. */
  hideUntilTerminal?: boolean;
}

export function OutcomeBanner({ outcome, hideUntilTerminal }: Props) {
  const { t } = useTranslation();
  if (!outcome || (hideUntilTerminal && outcome.verdict === "unknown")) {
    return null;
  }

  const variant = VERDICT_VARIANT[outcome.verdict] ?? "default";
  const glyph = VERDICT_GLYPH[outcome.verdict] ?? "…";
  const titleKey = `scene.outcome.${outcome.verdict}.title`;

  // Background tinting comes from the same brand-color tokens the rest
  // of the SPA uses (no inline hex). Sharp corners enforced via
  // --radius: 0 in globals.css — sharp-edged cards land naturally.
  const tintBg: Record<Variant, string> = {
    ok: "bg-ok/10 border-ok",
    warn: "bg-warn/10 border-warn",
    danger: "bg-danger/10 border-danger",
    info: "bg-info/10 border-info",
    default: "bg-muted border-border",
  };

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={outcome.verdict}
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: 0.25, ease: "easeOut" }}
      >
        <Card className={cn("border-l-4 p-4", tintBg[variant])}>
          <div className="flex items-start gap-3">
            <div
              className={cn(
                "flex h-12 w-12 shrink-0 items-center justify-center text-2xl font-bold",
                variant === "ok" && "text-ok",
                variant === "warn" && "text-warn",
                variant === "danger" && "text-danger",
                variant === "info" && "text-info",
              )}
              aria-hidden
            >
              {glyph}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold uppercase tracking-wide">
                  {t(titleKey, {
                    defaultValue: VERDICT_LABEL[outcome.verdict],
                  })}
                </h2>
                {outcome.tool ? (
                  <Badge variant={variant === "default" ? "secondary" : variant}>
                    {outcome.tool}
                  </Badge>
                ) : null}
                {outcome.time_to_outcome_seconds != null ? (
                  <Badge variant="secondary" className="font-mono">
                    {formatDuration(outcome.time_to_outcome_seconds)}
                  </Badge>
                ) : null}
              </div>
              <p className="mt-1 text-sm text-foreground/90">{outcome.reason}</p>
              {outcome.rule || outcome.confidence != null ? (
                <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  {outcome.rule ? (
                    <span>
                      <span className="opacity-60">
                        {t("scene.outcome.ruleLabel", { defaultValue: "rule" })}:
                      </span>{" "}
                      <span className="font-mono">{outcome.rule}</span>
                    </span>
                  ) : null}
                  {outcome.confidence != null ? (
                    <span>
                      <span className="opacity-60">
                        {t("scene.outcome.confidenceLabel", { defaultValue: "confidence" })}:
                      </span>{" "}
                      <span className="font-mono">{outcome.confidence.toFixed(2)}</span>
                    </span>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </Card>
      </motion.div>
    </AnimatePresence>
  );
}

function formatDuration(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const sec = Math.round(s - m * 60);
  return `${m}m ${sec}s`;
}
