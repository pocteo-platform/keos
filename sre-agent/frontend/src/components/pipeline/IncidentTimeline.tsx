import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Activity, Brain, CheckCircle, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { LOOP_PHASES, PHASE_VARIANT, type LoopPhase } from "@/lib/branding";
import { phaseOfNode } from "@/hooks/usePhaseGrouping";
import { NODE_ORDER, type NodeKind } from "@/lib/pipeline-topology";
import type { NodeStatus, PipelineState } from "@/hooks/usePipelineState";

// Badge accepts a fixed variant union; PHASE_VARIANT["heal"] = "primary"
// isn't in that union, so we map it to "default" (which IS the brand
// primary colour via the shadcn Badge defaults).
type BadgeVariant = "warn" | "info" | "ok" | "default";
const phaseBadgeVariant = (phase: LoopPhase): BadgeVariant => {
  const v = PHASE_VARIANT[phase];
  return v === "primary" ? "default" : (v as BadgeVariant);
};

interface Props {
  state: PipelineState;
  onSelectNode?: (kind: NodeKind) => void;
}

const PHASE_ICON: Record<LoopPhase, typeof Activity> = {
  observe: Activity,
  predict: Brain,
  heal: Wrench,
  learn: CheckCircle,
};

const STATUS_ACCENT: Record<NodeStatus, string> = {
  pending: "border-l-muted-foreground/30",
  active: "border-l-info animate-node-pulse",
  done: "border-l-ok",
  error: "border-l-danger",
  skipped: "border-l-muted-foreground/20 opacity-60",
};

/** Vertical Observe → Predict → Heal → Learn timeline.
 *
 *  One row per LangGraph node that has been reached (status !== pending),
 *  grouped under phase chips with the relative timestamp ("T+02:14")
 *  computed from the reducer's t0 anchor. Phase chips ALSO render when
 *  empty, so the operator sees the whole skeleton from the first event. */
function IncidentTimelineRaw({ state, onSelectNode }: Props) {
  const { t } = useTranslation();

  // Group nodes by phase, preserving NODE_ORDER within each phase.
  const byPhase = useMemo(() => {
    const map: Record<LoopPhase, NodeKind[]> = {
      observe: [],
      predict: [],
      heal: [],
      learn: [],
    };
    for (const k of NODE_ORDER) map[phaseOfNode(k)].push(k);
    return map;
  }, []);

  return (
    <div className="space-y-6">
      {LOOP_PHASES.map((phase) => {
        const Icon = PHASE_ICON[phase];
        const variant = phaseBadgeVariant(phase);
        const nodes = byPhase[phase];
        return (
          <section key={phase}>
            <header className="mb-3 flex items-center gap-2">
              <Badge variant={variant} className="gap-1 uppercase tracking-wider">
                <Icon className="h-3 w-3" />
                {t(`pipeline.timeline.phases.${phase}`)}
              </Badge>
            </header>
            <ol className="space-y-2">
              {nodes.map((kind) => (
                <TimelineCard
                  key={kind}
                  kind={kind}
                  node={state.nodes[kind]}
                  onSelectNode={onSelectNode}
                />
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

interface CardProps {
  kind: NodeKind;
  node: PipelineState["nodes"][NodeKind];
  onSelectNode?: (kind: NodeKind) => void;
}

function TimelineCard({ kind, node, onSelectNode }: CardProps) {
  const { t } = useTranslation();
  const rel = node.startedAt != null ? formatRel(node.startedAt) : null;
  const elapsed = node.elapsedMs != null ? formatMs(node.elapsedMs) : null;
  const summary = previewFor(kind, node.output);

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelectNode?.(kind)}
        className={cn(
          "w-full border-l-2 bg-card pl-4 pr-3 py-2 text-left transition-colors hover:bg-accent",
          STATUS_ACCENT[node.status],
        )}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className="text-[10px] font-mono uppercase text-muted-foreground w-14">
              {rel ?? "—"}
            </span>
            <span className="font-semibold">{t(`pipeline.nodes.${kind}.title`)}</span>
          </div>
          {elapsed ? (
            <span className="font-mono text-[10px] text-muted-foreground">{elapsed}</span>
          ) : null}
        </div>
        <p className="mt-1 ml-16 text-[11px] leading-snug text-muted-foreground line-clamp-2">
          {summary ?? t(`pipeline.nodes.${kind}.desc`)}
        </p>
      </button>
    </li>
  );
}

/** "T+02:14" style relative timestamp from a millisecond offset. */
function formatRel(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `T+${mm}:${ss}`;
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function previewFor(kind: NodeKind, output?: Record<string, unknown>): string | undefined {
  if (!output) return undefined;
  if (kind === "diagnose") {
    const d = (output.diagnosis as { confidence?: number; summary?: string }) ?? {};
    if (d.summary) return `conf=${d.confidence ?? "?"} · ${d.summary}`;
  }
  if (kind === "plan_action") {
    const p = (output.proposed_action as { tool?: string; args?: Record<string, unknown> }) ?? {};
    if (p.tool) return `${p.tool}(${Object.keys(p.args ?? {}).length} args)`;
  }
  if (kind === "policy_gate") {
    const d = (output.policy_decision as { allow?: boolean; reasons?: string[] }) ?? {};
    if (d.allow === false && d.reasons?.length) return `DENY · ${d.reasons[0]}`;
    if (d.allow === true) return "ALLOW";
  }
  if (kind === "execute") {
    const r = (output.action_result as { success?: boolean; stdout?: string }) ?? {};
    if (r.success === false) return "FAIL";
    if (r.stdout) return r.stdout;
  }
  if (kind === "verify") {
    const v = (output.verification as { healed?: boolean; notes?: string }) ?? {};
    if (v.notes) return `healed=${v.healed} · ${v.notes}`;
  }
  return undefined;
}

export const IncidentTimeline = memo(IncidentTimelineRaw);
