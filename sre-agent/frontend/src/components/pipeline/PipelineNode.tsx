import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import * as Icons from "lucide-react";
import { cn } from "@/lib/utils";
import type { NodeKind } from "@/lib/pipeline-topology";
import type { NodeStatus } from "@/hooks/usePipelineState";

type LucideIcon = React.ComponentType<{ className?: string }>;

export interface PipelineNodeData extends Record<string, unknown> {
  kind: NodeKind;
  icon: string;
  status: NodeStatus;
  elapsedMs?: number;
  preview?: string;
  onClick?: () => void;
}

const STATUS_RING: Record<NodeStatus, string> = {
  pending: "ring-border bg-card",
  active: "ring-info bg-info/10 animate-node-pulse",
  done: "ring-ok bg-ok/10",
  error: "ring-danger bg-danger/10",
  skipped: "ring-border bg-muted/30 opacity-70",
};

const STATUS_DOT: Record<NodeStatus, string> = {
  pending: "bg-muted-foreground/40",
  active: "bg-info",
  done: "bg-ok",
  error: "bg-danger",
  skipped: "bg-muted-foreground/30",
};

function PipelineNodeRaw({ data }: NodeProps) {
  const d = data as PipelineNodeData;
  const { t } = useTranslation();
  const Icon = ((Icons as unknown as Record<string, LucideIcon>)[d.icon] ??
    Icons.Circle) as LucideIcon;
  const titleKey = `pipeline.nodes.${d.kind}.title`;
  const descKey = `pipeline.nodes.${d.kind}.desc`;

  return (
    <button
      type="button"
      onClick={d.onClick}
      className={cn(
        "w-60 rounded-lg border-2 p-3 text-left shadow-sm transition-all",
        "ring-1 ring-inset",
        STATUS_RING[d.status],
        d.kind === "escalate" ? "border-warn/40" : "border-border",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !bg-muted-foreground" />
      <div className="flex items-center gap-2">
        <span className={cn("inline-block h-2 w-2 rounded-full", STATUS_DOT[d.status])} />
        <Icon className="h-4 w-4 text-foreground/80" />
        <span className="flex-1 truncate font-medium text-sm">{t(titleKey)}</span>
        {d.elapsedMs != null ? (
          <span className="font-mono text-[10px] text-muted-foreground">
            {formatMs(d.elapsedMs)}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-[11px] leading-snug text-muted-foreground line-clamp-2">
        {t(descKey)}
      </p>
      {d.preview ? (
        <p className="mt-2 text-[10px] leading-tight text-muted-foreground/80 line-clamp-2 font-mono">
          {d.preview}
        </p>
      ) : null}
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !bg-muted-foreground" />
      {d.kind === "escalate" ? (
        <Handle id="esc-in" type="target" position={Position.Left} className="!h-2 !w-2 !bg-warn" />
      ) : null}
    </button>
  );
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// Memo guard: only re-render when the status or elapsed time change. The
// canvas may receive WS events 10+ Hz; without memo the whole graph
// reflows on every event.
export const PipelineNode = memo(PipelineNodeRaw, (a, b) => {
  const da = a.data as PipelineNodeData;
  const db = b.data as PipelineNodeData;
  return (
    da.status === db.status &&
    da.elapsedMs === db.elapsedMs &&
    da.preview === db.preview &&
    da.onClick === db.onClick
  );
});
