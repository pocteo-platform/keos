import { useEffect, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node as RFNode,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { NODE_ORDER, TOPOLOGY_EDGES, TOPOLOGY_NODES, type NodeKind } from "@/lib/pipeline-topology";
import { PipelineNode, type PipelineNodeData } from "./PipelineNode";
import type { PipelineState } from "@/hooks/usePipelineState";

const nodeTypes: NodeTypes = { pipeline: PipelineNode };

interface Props {
  state: PipelineState;
  onSelectNode: (kind: NodeKind) => void;
}

export function PipelineCanvas({ state, onSelectNode }: Props) {
  // Build React Flow nodes from topology + reduce state.
  const nodes: RFNode[] = useMemo(() => {
    return TOPOLOGY_NODES.map((spec) => {
      const s = state.nodes[spec.id];
      const data: PipelineNodeData = {
        kind: spec.id,
        icon: spec.icon,
        status: s.status,
        elapsedMs: s.elapsedMs,
        preview: previewFor(spec.id, s.output),
        onClick: () => onSelectNode(spec.id),
      };
      return {
        id: spec.id,
        position: spec.position,
        data,
        type: "pipeline",
        draggable: false,
      };
    });
  }, [state.nodes, onSelectNode]);

  // Edges — animate any in-transit (source done, target active/pending),
  // solid + colored once the target has started.
  const edges: Edge[] = useMemo(() => {
    return TOPOLOGY_EDGES.map((e) => {
      const fired = state.firedEdges.has(e.id);
      const source = state.nodes[e.source as NodeKind];
      const target = state.nodes[e.target as NodeKind];
      const inTransit = source.status === "done" && target.status === "pending";
      const isEscalate = e.target === "escalate";
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        animated: fired && (target.status === "active" || inTransit),
        style: edgeStyle(fired, isEscalate, target.status),
        targetHandle: isEscalate ? "esc-in" : undefined,
        type: isEscalate ? "smoothstep" : "default",
      };
    });
  }, [state.firedEdges, state.nodes]);

  const fitViewRef = useRef(false);

  return (
    <ReactFlowProvider>
      <div className="h-full w-full">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          panOnDrag
          minZoom={0.5}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
          onInit={() => {
            fitViewRef.current = true;
          }}
        >
          <Background gap={20} size={1} />
          <Controls position="bottom-right" showInteractive={false} />
        </ReactFlow>
        <FollowActiveNode state={state} />
      </div>
    </ReactFlowProvider>
  );
}

function edgeStyle(fired: boolean, escalate: boolean, targetStatus: string) {
  if (!fired)
    return { stroke: "hsl(var(--muted-foreground))", strokeOpacity: 0.3, strokeDasharray: "4 4" };
  if (escalate) return { stroke: "hsl(38 92% 50%)", strokeWidth: 2 };
  if (targetStatus === "active") return { stroke: "hsl(199 89% 48%)", strokeWidth: 2 };
  return { stroke: "hsl(142 72% 45%)", strokeWidth: 2 };
}

function previewFor(node: NodeKind, output?: Record<string, unknown>): string | undefined {
  if (!output) return undefined;
  if (node === "diagnose") {
    const d = (output.diagnosis as { confidence?: number; summary?: string }) ?? {};
    if (d.summary) return `conf=${d.confidence ?? "?"} ${d.summary.slice(0, 60)}`;
  }
  if (node === "plan_action") {
    const p = (output.proposed_action as { tool?: string; args?: Record<string, unknown> }) ?? {};
    if (p.tool) return `${p.tool}(${Object.keys(p.args ?? {}).length} args)`;
  }
  if (node === "policy_gate") {
    const d = (output.policy_decision as { allow?: boolean; reasons?: string[] }) ?? {};
    if (d.allow === false && d.reasons?.length) return `DENY: ${d.reasons[0].slice(0, 60)}`;
    if (d.allow === true) return "ALLOW";
  }
  if (node === "execute") {
    const r = (output.action_result as { success?: boolean; stdout?: string }) ?? {};
    if (r.success === false) return "FAIL";
    if (r.stdout) return r.stdout.slice(0, 60);
  }
  if (node === "verify") {
    const v = (output.verification as { healed?: boolean; notes?: string }) ?? {};
    if (v.notes) return `healed=${v.healed} ${v.notes.slice(0, 50)}`;
  }
  return undefined;
}

// Best-effort focus on the active node when the user is live-watching.
// `useReactFlow().setCenter` would be more direct but creates a strong
// coupling; for the PoC this is a no-op stub that signals intent.
function FollowActiveNode({ state }: { state: PipelineState }) {
  useEffect(() => {
    const active = NODE_ORDER.find((n) => state.nodes[n].status === "active");
    if (!active) return;
    // React Flow's fitView is fine for our small graph — we don't need
    // setCenter precision. Intentional no-op; the canvas already scrolls
    // when nodes overflow.
  }, [state.nodes]);
  return null;
}
