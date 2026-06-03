import { useTranslation } from "react-i18next";
import { Eye, Brain, Hammer, GraduationCap, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { LOOP_PHASES, type LoopPhase } from "@/lib/branding";
import type { PhaseState } from "./computePhaseStates";

/** Pro loader shown while the agent processes an incident.
 *
 *  Driven by REAL trace events, not a dumb timer. The parent subscribes
 *  to the `pipeline.<incident_id>` WebSocket channel and feeds the
 *  observed nodes here. We map nodes → phases via `PHASE_BY_NODE` so
 *  the loader reflects what the agent is *actually* doing right now:
 *
 *    triage / retrieve_context / k8sgpt_analyze  → OBSERVE
 *    diagnose / plan_action                      → PREDICT
 *    policy_gate / execute                       → HEAL
 *    verify / escalate                           → LEARN
 *
 *  A phase is:
 *    - DONE  if any node in a LATER phase has started
 *    - ACTIVE if its own latest node has only `start` (no `end` yet)
 *    - DONE  if its own latest node has `end`
 *    - PENDING otherwise
 */

const ICONS: Record<LoopPhase, typeof Eye> = {
  observe: Eye,
  predict: Brain,
  heal: Hammer,
  learn: GraduationCap,
};

export interface RunningIndicatorProps {
  /** Per-phase state. Parent computes this from WS trace events. */
  phases?: Record<LoopPhase, PhaseState>;
  /** Optional current node label ("retrieve_context", "diagnose", ...).
   *  Shown under the title so the user sees real-time progress. */
  currentNode?: string | null;
  subtitle?: string;
}

const DEFAULT_PHASES: Record<LoopPhase, PhaseState> = {
  observe: "active",
  predict: "pending",
  heal: "pending",
  learn: "pending",
};

export function RunningIndicator({
  phases = DEFAULT_PHASES,
  currentNode,
  subtitle,
}: RunningIndicatorProps) {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col items-center justify-center gap-8 py-16">
      <div className="flex flex-col items-center gap-2 text-center">
        <h2 className="text-xl font-semibold">
          {t("pipeline.running.title", "Agent is processing")}
        </h2>
        {currentNode ? (
          <p className="font-mono text-sm text-primary">
            {t(`pipeline.nodes.${currentNode}.title`, currentNode)}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            {subtitle ??
              t(
                "pipeline.running.subtitle",
                "Waiting for the first trace event from the agent loop",
              )}
          </p>
        )}
      </div>

      {/* Phase carousel — state derived from real trace events */}
      <div className="flex items-center gap-3 sm:gap-6">
        {LOOP_PHASES.map((phase) => {
          const Icon = ICONS[phase];
          const state = phases[phase];
          const isActive = state === "active";
          const isDone = state === "done";
          return (
            <div key={phase} className="flex flex-col items-center gap-2">
              <div
                className={cn(
                  "relative flex h-14 w-14 items-center justify-center border-2 transition-all duration-300 sm:h-16 sm:w-16",
                  isActive && "scale-110 border-primary bg-primary/10",
                  isDone && "border-ok bg-ok/10",
                  !isActive && !isDone && "border-border bg-card",
                )}
              >
                {isDone ? (
                  <Check className="h-6 w-6 text-ok sm:h-7 sm:w-7" />
                ) : (
                  <Icon
                    className={cn(
                      "h-6 w-6 transition-colors sm:h-7 sm:w-7",
                      isActive && "text-primary",
                      !isActive && "text-muted-foreground",
                    )}
                  />
                )}
                {/* Pulse ring on active phase */}
                {isActive ? (
                  <span className="pointer-events-none absolute inset-0 animate-ping border-2 border-primary opacity-75" />
                ) : null}
              </div>
              <span
                className={cn(
                  "text-[10px] font-semibold uppercase tracking-wider transition-colors",
                  isActive && "text-primary",
                  isDone && "text-ok",
                  !isActive && !isDone && "text-muted-foreground",
                )}
              >
                {t(`pipeline.timeline.phases.${phase}`)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Indeterminate sliding bar — visual heartbeat so the user can tell
          we're still alive even if the LLM is taking ~30s between trace events. */}
      <div className="relative h-1 w-64 overflow-hidden bg-border">
        <div
          className="absolute inset-y-0 w-1/3 bg-primary"
          style={{ animation: "running-slide 1.6s ease-in-out infinite" }}
        />
      </div>
      <style>{`
        @keyframes running-slide {
          0%   { transform: translateX(-100%); }
          50%  { transform: translateX(220%); }
          100% { transform: translateX(220%); }
        }
      `}</style>
    </div>
  );
}

/** Compact inline spinner — for use inside buttons / table cells. */
export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="loading"
      className={cn(
        "inline-block h-3.5 w-3.5 animate-spin border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}
