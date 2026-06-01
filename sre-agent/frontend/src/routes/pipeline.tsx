import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { useChannel } from "@/lib/ws";
import { usePipelineState, type TraceEvent } from "@/hooks/usePipelineState";
import type { NodeKind } from "@/lib/pipeline-topology";
import { PipelineCanvas } from "@/components/pipeline/PipelineCanvas";
import { NodeDrawer } from "@/components/pipeline/NodeDrawer";
import { Scrubber } from "@/components/pipeline/Scrubber";
import { IncidentPicker } from "@/components/pipeline/IncidentPicker";
import { IncidentTimeline } from "@/components/pipeline/IncidentTimeline";
import { TelemetrySidebar } from "@/components/pipeline/TelemetrySidebar";
import { RunningIndicator, computePhaseStates } from "@/components/pipeline/RunningIndicator";
import { ResultsBanner } from "@/components/usecases/ResultsBanner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { ActiveIncident } from "@/lib/api";

type Speed = 0.5 | 1 | 2 | 4 | "instant";

export default function PipelinePage() {
  const params = useParams<{ id?: string }>();
  const incidentId = params.id ?? null; // null for /pipeline/live
  const isLive = incidentId === null || incidentId === "live";

  // ---- LIVE MODE: subscribe to WS, append events as they arrive --------
  if (isLive) return <LiveCanvas />;
  // ---- REPLAY MODE: fetch the incident, scrub through trace records ----
  return <ReplayCanvas incidentId={incidentId!} />;
}

// ---------------------------------------------------------------------------
// LIVE — wildcard subscription, last incident wins
// ---------------------------------------------------------------------------

function LiveCanvas() {
  const { t } = useTranslation();
  const { state, apply, reset } = usePipelineState();
  const [selected, setSelected] = useState<NodeKind | null>(null);

  // Two pieces of state for the picker:
  //  - `following`: which incident_id the canvas is currently tracking
  //    (null means "auto-follow newest").
  //  - `seen`: every incident_id we've observed on the WS this session,
  //    with a small denormalised summary for the dropdown.
  const [following, setFollowing] = useState<string | null>(null);
  const [seen, setSeen] = useState<ActiveIncident[]>([]);
  const lockedRef = useRef<string | null>(null);

  // Subscribe to all pipeline events. The reducer is only fed events for
  // the currently-followed incident — switching the picker resets the
  // canvas and starts feeding the new id's events.
  useChannel("pipeline.*", (event) => {
    const ev = event as unknown as TraceEvent;
    const id = ev.incident_id ?? null;
    if (!id) return;

    // Update the "seen this session" list (denormalised summary).
    setSeen((prev) => {
      const idx = prev.findIndex((p) => p.incident_id === id);
      const snap = (ev.input_snapshot ?? {}) as Record<string, unknown>;
      const summary = {
        incident_id: id,
        rule: (snap.rule as string) ?? prev[idx]?.rule ?? null,
        severity: (snap.severity as string) ?? prev[idx]?.severity ?? null,
        pod: (snap.pod as string) ?? prev[idx]?.pod ?? null,
        namespace: (snap.namespace as string) ?? prev[idx]?.namespace ?? null,
        started_at: prev[idx]?.started_at ?? ev.ts ?? null,
      } as ActiveIncident;
      if (idx === -1) return [...prev, summary].slice(-20);
      const next = [...prev];
      next[idx] = summary;
      return next;
    });

    // Determine the effective followed id.
    const target = following ?? id; // null following → auto-follow current
    if (id !== target) return; // event belongs to another incident; ignore

    // Resetting when the followed incident changes (auto-mode picking up a
    // brand-new id, OR the user switching the picker).
    if (lockedRef.current !== target) {
      lockedRef.current = target;
      reset();
    }
    apply(ev);
  });

  // When the picker switches manually, clear the canvas so the next event
  // for that incident triggers a clean re-feed.
  const onPickerChange = (id: string | null) => {
    setFollowing(id);
    lockedRef.current = null;
    reset();
  };

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-4">
      <Header
        title={t("pipeline.title")}
        subtitle={t("pipeline.live.subtitle")}
        right={
          <Badge variant="info" className="gap-1">
            <span className="h-2 w-2 animate-node-pulse rounded-full bg-info" />
            {t("topbar.live")}
          </Badge>
        }
      />
      <IncidentPicker current={following} seen={seen} onChange={onPickerChange} />
      <Tabs defaultValue="timeline" className="flex-1 flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="timeline">{t("pipeline.tabs.timeline")}</TabsTrigger>
          <TabsTrigger value="graph">{t("pipeline.tabs.graph")}</TabsTrigger>
        </TabsList>
        <TabsContent value="timeline" className="flex-1">
          <div className="grid h-full grid-cols-1 gap-4 overflow-auto lg:grid-cols-[1fr_22rem]">
            <Card>
              <CardContent className="p-6">
                <IncidentTimeline state={state} onSelectNode={setSelected} />
              </CardContent>
            </Card>
            <TelemetrySidebar state={state} />
          </div>
        </TabsContent>
        <TabsContent value="graph" className="flex-1">
          <Card className="h-full">
            <CardContent className="h-full p-0">
              <PipelineCanvas state={state} onSelectNode={setSelected} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
      <NodeDrawer
        kind={selected}
        state={selected ? state.nodes[selected] : null}
        open={Boolean(selected)}
        onOpenChange={(o) => !o && setSelected(null)}
      />
      {state.incidentId ? (
        <p className="text-xs text-muted-foreground">
          {t("pipeline.live.watching")} <span className="font-mono">{state.incidentId}</span>
        </p>
      ) : (
        <Card>
          <CardContent className="py-6 text-center text-sm text-muted-foreground">
            <Activity className="mx-auto mb-2 h-5 w-5" />
            {t("pipeline.live.empty")}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// REPLAY — fetch the incident detail, scrub through trace records
// ---------------------------------------------------------------------------

function ReplayCanvas({ incidentId }: { incidentId: string }) {
  const { t } = useTranslation();
  const { state, apply, reset } = usePipelineState();
  const [selected, setSelected] = useState<NodeKind | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<Speed>(1);
  const [cursorMs, setCursorMs] = useState(0);
  const lastIdxRef = useRef(-1);
  const [searchParams] = useSearchParams();
  const useCaseId = searchParams.get("usecase");

  // Auto-poll while the incident is still warming up. Two important
  // behaviors:
  //
  //  1. `retry: false` — `getIncident` returns 404 while the graph hasn't
  //     yet saved an incident file. Default TanStack Query retries 3
  //     times on error, each retry flips `isLoading` true → that's what
  //     made the loader flicker between "Loading…" text and the indicator.
  //  2. `refetchInterval` polls every 2s, but `isLoading` ONLY ever fires
  //     on the very first fetch (with `placeholderData`, even that goes
  //     away). Subsequent polls keep showing the current data.
  //
  // We give the query an empty placeholder so the loader takes over
  // immediately without ever rendering the generic "Loading…" text.
  const incidentQ = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => api.getIncident(incidentId),
    retry: false,
    placeholderData: { incident_id: incidentId, pipeline_trace: [] } as never,
    refetchInterval: (q) => {
      const data = q.state.data as { pipeline_trace?: unknown[] } | undefined;
      const persisted = (data?.pipeline_trace?.length ?? 0) > 0;
      // Stop polling once the agent has persisted the final incident.
      return persisted ? false : 2000;
    },
  });

  // Live trace events while the incident is in-flight. The agent
  // publishes each node start/end through the WebSocket TraceBus on
  // channel `pipeline.<incident_id>`. We accumulate them locally so the
  // RunningIndicator + IncidentTimeline get real-time updates without
  // waiting for the next 2s poll.
  const [liveEvents, setLiveEvents] = useState<TraceEvent[]>([]);
  useChannel(`pipeline.${incidentId}`, (event) => {
    setLiveEvents((prev) => [...prev, event as unknown as TraceEvent]);
  });

  const trace = useMemo<TraceEvent[]>(() => {
    const persisted = (incidentQ.data?.pipeline_trace ?? []) as unknown as TraceEvent[];
    // Merge persisted + live. Dedup by (node, phase, ts) so we don't get
    // duplicate cards when a live event later shows up in the polled set.
    const seen = new Set<string>();
    const out: TraceEvent[] = [];
    for (const ev of [...persisted, ...liveEvents]) {
      const key = `${ev.node ?? ""}-${ev.phase ?? ""}-${ev.ts ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(ev);
    }
    return out.sort((a, b) => tsCmp(a.ts, b.ts));
  }, [incidentQ.data?.pipeline_trace, liveEvents]);

  // Map trace → phase states for the RunningIndicator.
  const phaseStates = useMemo(() => {
    const seen = trace
      .filter((e) => e.node && (e.phase === "start" || e.phase === "end"))
      .map((e) => ({ kind: e.node as string, phase: e.phase as "start" | "end" }));
    return computePhaseStates(seen);
  }, [trace]);

  // Most recent node that started but hasn't ended — shown as a label
  // in the loader so the user can SEE which node is grinding.
  const currentNode = useMemo<string | null>(() => {
    const startedButNotEnded = new Map<string, boolean>();
    for (const e of trace) {
      if (!e.node) continue;
      if (e.phase === "start") startedButNotEnded.set(e.node as string, true);
      else if (e.phase === "end") startedButNotEnded.delete(e.node as string);
    }
    const keys = Array.from(startedButNotEnded.keys());
    return keys.length ? keys[keys.length - 1] : null;
  }, [trace]);

  const totalMs = useMemo(() => {
    if (trace.length < 2) return 0;
    const first = tsToMs(trace[0].ts);
    const last = tsToMs(trace[trace.length - 1].ts);
    return Math.max(0, last - first);
  }, [trace]);

  // Apply events up to the cursor whenever cursorMs changes.
  useEffect(() => {
    if (trace.length === 0) return;
    const first = tsToMs(trace[0].ts);
    let lastIdx = -1;
    for (let i = 0; i < trace.length; i++) {
      const t = tsToMs(trace[i].ts) - first;
      if (t <= cursorMs) lastIdx = i;
      else break;
    }
    if (lastIdx === lastIdxRef.current) return;
    // Rewinds reset + replay; forward just appends.
    if (lastIdx < lastIdxRef.current) {
      reset();
      for (let i = 0; i <= lastIdx; i++) apply(trace[i]);
    } else {
      for (let i = lastIdxRef.current + 1; i <= lastIdx; i++) apply(trace[i]);
    }
    lastIdxRef.current = lastIdx;
  }, [cursorMs, trace, apply, reset]);

  // Tick the cursor while playing.
  useEffect(() => {
    if (!playing || totalMs === 0) return;
    if (speed === "instant") {
      setCursorMs(totalMs);
      setPlaying(false);
      return;
    }
    const intervalMs = 100;
    const advanceMs = intervalMs * (speed as number);
    const handle = window.setInterval(() => {
      setCursorMs((c) => {
        const n = c + advanceMs;
        if (n >= totalMs) {
          setPlaying(false);
          return totalMs;
        }
        return n;
      });
    }, intervalMs);
    return () => window.clearInterval(handle);
  }, [playing, speed, totalMs]);

  const onStep = () => {
    if (trace.length === 0) return;
    const first = tsToMs(trace[0].ts);
    for (const ev of trace) {
      const tMs = tsToMs(ev.ts) - first;
      if (tMs > cursorMs) {
        setCursorMs(tMs);
        break;
      }
    }
  };

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-4">
      <Header
        title={t("pipeline.title")}
        subtitle={
          <>
            {t("pipeline.replay.watching")} <span className="font-mono">{incidentId}</span>
          </>
        }
      />
      {useCaseId && incidentQ.data ? (
        <ResultsBanner useCaseId={useCaseId} incident={incidentQ.data} />
      ) : null}
      <Tabs defaultValue="timeline" className="flex-1 flex flex-col">
        <TabsList className="self-start">
          <TabsTrigger value="timeline">{t("pipeline.tabs.timeline")}</TabsTrigger>
          <TabsTrigger value="graph">{t("pipeline.tabs.graph")}</TabsTrigger>
        </TabsList>
        <TabsContent value="timeline" className="flex-1">
          <div className="grid h-full grid-cols-1 gap-4 overflow-auto lg:grid-cols-[1fr_22rem]">
            <Card>
              <CardContent className="p-6">
                {trace.length === 0 ? (
                  // No trace yet. If we got here from a Run redirect OR
                  // the incident file isn't persisted (404 → placeholder),
                  // show the live loader. The loader keeps showing real
                  // node progress as WS events arrive, then is replaced
                  // by IncidentTimeline once any trace records exist.
                  <RunningIndicator phases={phaseStates} currentNode={currentNode} />
                ) : (
                  <IncidentTimeline state={state} onSelectNode={setSelected} />
                )}
              </CardContent>
            </Card>
            <TelemetrySidebar state={state} />
          </div>
        </TabsContent>
        <TabsContent value="graph" className="flex-1">
          <Card className="h-full">
            <CardContent className="h-full p-0">
              {trace.length === 0 ? (
                <div className="flex h-full items-center justify-center p-6">
                  <RunningIndicator phases={phaseStates} currentNode={currentNode} />
                </div>
              ) : (
                <PipelineCanvas state={state} onSelectNode={setSelected} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
      {trace.length > 0 ? (
        <Scrubber
          totalMs={totalMs}
          cursorMs={cursorMs}
          playing={playing}
          speed={speed}
          onSeek={(ms) => {
            setCursorMs(ms);
            setPlaying(false);
          }}
          onTogglePlay={() => setPlaying((p) => !p)}
          onStep={() => {
            setPlaying(false);
            onStep();
          }}
          onSpeed={setSpeed}
        />
      ) : null}
      <NodeDrawer
        kind={selected}
        state={selected ? state.nodes[selected] : null}
        open={Boolean(selected)}
        onOpenChange={(o) => !o && setSelected(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------

function Header({
  title,
  subtitle,
  right,
}: {
  title: React.ReactNode;
  subtitle: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 py-4">
        <div>
          <CardTitle className="text-xl">{title}</CardTitle>
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        </div>
        {right}
      </CardHeader>
    </Card>
  );
}

const tsToMs = (ts?: string): number => (ts ? new Date(ts).getTime() : 0);
const tsCmp = (a?: string, b?: string) => tsToMs(a) - tsToMs(b);
