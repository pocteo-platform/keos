import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, type AgentMode } from "@/lib/api";
import { ModeBadge } from "@/components/kube/ModeBadge";

const MODES: AgentMode[] = ["observe-only", "active", "frozen"];

/**
 * Settings → Agent tab. Reuses what was on the old /control page:
 * mode toggle + pending approvals queue. The kill-switch story
 * (observe-only ↔ active ↔ frozen) is the most important live
 * control surface; keeping it in Settings rather than scattered
 * across the SPA matches the rest of the Kubernetes ecosystem
 * (Komodor "Cluster controls", Datadog "Setup" page).
 */
export function AgentTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [confirmMode, setConfirmMode] = useState<AgentMode | null>(null);
  const [denyReason, setDenyReason] = useState<Record<string, string>>({});

  const modeQ = useQuery({ queryKey: ["mode"], queryFn: api.getMode });
  const approvalsQ = useQuery({
    queryKey: ["approvals"],
    queryFn: api.listApprovals,
    refetchInterval: 4_000,
  });

  const setModeMut = useMutation({
    mutationFn: (m: AgentMode) => api.setMode(m),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["mode"] });
      setConfirmMode(null);
    },
  });
  const approveMut = useMutation({
    mutationFn: (id: string) => api.approveAction(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
  const denyMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.denyAction(id, reason),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["approvals"] }),
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Mode toggle */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
            {t("settings.agent.mode.title", { defaultValue: "Agent mode" })}
            {modeQ.data ? <ModeBadge mode={modeQ.data.mode} /> : null}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {t("settings.agent.mode.description", {
              defaultValue:
                "Kill-switch for the agent. `observe-only` is dry-run; `active` lets actions execute; `frozen` blocks everything including paging.",
            })}
          </p>
          <ToggleGroup
            type="single"
            value={modeQ.data?.mode ?? ""}
            onValueChange={(v) => {
              if (!v) return;
              setConfirmMode(v as AgentMode);
            }}
            className="self-start"
          >
            {MODES.map((m) => (
              <ToggleGroupItem key={m} value={m} className="px-3">
                {t(`settings.agent.mode.option.${m}`, { defaultValue: m })}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </CardContent>
      </Card>

      {/* Pending approvals */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm uppercase tracking-wider">
            {t("settings.agent.approvals.title", { defaultValue: "Pending approvals" })}
            {(approvalsQ.data?.length ?? 0) > 0 ? (
              <Badge variant="warn">{approvalsQ.data?.length}</Badge>
            ) : null}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {(approvalsQ.data?.length ?? 0) === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("settings.agent.approvals.empty", {
                defaultValue: "No actions waiting on operator approval.",
              })}
            </p>
          ) : (
            <ul className="space-y-2">
              {(approvalsQ.data ?? []).map((a) => {
                // `proposed_action` is `Record<string, unknown>` in the
                // typed client; cast the fields we read so TS lets us
                // render strings without forcing every site to handle
                // the wider type. The agent guarantees these keys when
                // an approval row is queued.
                const action = a.proposed_action as {
                  tool?: string;
                  rationale?: string;
                  args?: { pod_name?: string; namespace?: string };
                };
                const pod = action.args?.pod_name ?? "—";
                return (
                  <li key={a.incident_id} className="border-l-2 border-warn bg-warn/5 px-3 py-2">
                  <div className="flex items-baseline gap-2 text-xs">
                    <Badge variant="warn" className="text-[10px] uppercase">
                      {action.tool ?? "—"}
                    </Badge>
                    <span className="font-mono">
                      {a.namespace ?? "—"}/{pod}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-foreground/90">{action.rationale ?? ""}</p>
                  <div className="mt-2 flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="default"
                      disabled={approveMut.isPending}
                      onClick={() => approveMut.mutate(a.incident_id)}
                    >
                      {t("settings.agent.approvals.approve", { defaultValue: "Approve" })}
                    </Button>
                    <Input
                      placeholder={t("settings.agent.approvals.denyReason", {
                        defaultValue: "Reason…",
                      })}
                      value={denyReason[a.incident_id] ?? ""}
                      onChange={(e) =>
                        setDenyReason({ ...denyReason, [a.incident_id]: e.target.value })
                      }
                      className="h-8 max-w-xs text-xs"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={denyMut.isPending || !denyReason[a.incident_id]}
                      onClick={() =>
                        denyMut.mutate({
                          id: a.incident_id,
                          reason: denyReason[a.incident_id] ?? "",
                        })
                      }
                    >
                      {t("settings.agent.approvals.deny", { defaultValue: "Deny" })}
                    </Button>
                  </div>
                </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Confirm-mode-change dialog */}
      <Dialog open={confirmMode !== null} onOpenChange={(o) => !o && setConfirmMode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t("settings.agent.confirmTitle", {
                defaultValue: `Switch agent to ${confirmMode}?`,
                mode: confirmMode,
              })}
            </DialogTitle>
            <DialogDescription>
              {confirmMode === "active"
                ? t("settings.agent.confirmActive", {
                    defaultValue:
                      "Active mode lets the agent execute remediations autonomously. OPA still gates every action.",
                  })
                : confirmMode === "frozen"
                  ? t("settings.agent.confirmFrozen", {
                      defaultValue:
                        "Frozen mode blocks ALL actions, including page_human. Use for incident response when you need full manual control.",
                    })
                  : t("settings.agent.confirmObserve", {
                      defaultValue: "Observe-only is dry-run; actions are logged but not executed.",
                    })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmMode(null)}>
              {t("common.cancel", { defaultValue: "Cancel" })}
            </Button>
            <Button
              onClick={() => confirmMode && setModeMut.mutate(confirmMode)}
              disabled={setModeMut.isPending}
            >
              {t("settings.agent.confirmApply", { defaultValue: "Apply" })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
