import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { AuditTable } from "@/components/audit/AuditTable";
import { api, type AgentMode } from "@/lib/api";

const MODES: AgentMode[] = ["observe-only", "active", "frozen"];

export default function ControlPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [confirmMode, setConfirmMode] = useState<AgentMode | null>(null);
  const [denyReason, setDenyReason] = useState<Record<string, string>>({});

  const modeQ = useQuery({ queryKey: ["mode"], queryFn: api.getMode });
  const approvalsQ = useQuery({
    queryKey: ["approvals"],
    queryFn: api.listApprovals,
    refetchInterval: 4000,
  });
  const auditQ = useQuery({
    queryKey: ["audit"],
    queryFn: () => api.listAudit({ limit: 200 }),
    refetchInterval: 5000,
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
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["approvals"] });
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const denyMut = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => api.denyAction(id, reason),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["approvals"] });
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t("control.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("control.subtitle")}</p>
      </div>

      {/* Mode toggle */}
      <Card>
        <CardHeader>
          <CardTitle>{t("control.mode.title")}</CardTitle>
          <CardDescription>{t("control.mode.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ToggleGroup
            type="single"
            value={modeQ.data?.mode}
            onValueChange={(v) => v && setConfirmMode(v as AgentMode)}
          >
            {MODES.map((m) => (
              <ToggleGroupItem key={m} value={m}>
                {t(`modes.${m}`)}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </CardContent>
      </Card>

      {/* Pending approvals */}
      <Card>
        <CardHeader>
          <CardTitle>{t("control.approvals.title")}</CardTitle>
          <CardDescription>{t("control.approvals.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {(approvalsQ.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("control.approvals.empty")}</p>
          ) : (
            <ul className="space-y-3">
              {(approvalsQ.data ?? []).map((a) => {
                const action = a.proposed_action as {
                  tool?: string;
                  args?: Record<string, unknown>;
                };
                return (
                  <li key={a.incident_id} className="rounded-md border p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="font-mono text-xs text-muted-foreground">
                          {a.incident_id}
                        </div>
                        <div className="mt-1 flex items-center gap-2">
                          <Badge variant="warn">{a.severity}</Badge>
                          <span className="font-medium">{action.tool}</span>
                          <code className="text-xs text-muted-foreground">
                            {JSON.stringify(action.args ?? {})}
                          </code>
                        </div>
                        <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                          {a.denied_reasons.map((r) => (
                            <li key={r}>· {r}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="flex flex-col items-end gap-2">
                        <Button
                          onClick={() => approveMut.mutate(a.incident_id)}
                          disabled={approveMut.isPending}
                        >
                          {t("control.approvals.approveOnce")}
                        </Button>
                        <div className="flex w-72 items-center gap-2">
                          <Input
                            placeholder={t("control.approvals.denyReason")}
                            value={denyReason[a.incident_id] ?? ""}
                            onChange={(e) =>
                              setDenyReason((s) => ({ ...s, [a.incident_id]: e.target.value }))
                            }
                            className="h-9"
                          />
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={
                              !(denyReason[a.incident_id] ?? "").length || denyMut.isPending
                            }
                            onClick={() =>
                              denyMut.mutate({
                                id: a.incident_id,
                                reason: denyReason[a.incident_id] ?? "",
                              })
                            }
                          >
                            {t("common.deny")}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Audit log */}
      <Card>
        <CardHeader>
          <CardTitle>{t("control.audit.title")}</CardTitle>
          <CardDescription>{t("control.audit.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <AuditTable records={auditQ.data?.items ?? []} />
        </CardContent>
      </Card>

      {/* Mode confirm dialog */}
      <Dialog open={Boolean(confirmMode)} onOpenChange={(o) => !o && setConfirmMode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirmMode
                ? t("control.mode.confirm.title", { mode: t(`modes.${confirmMode}`) })
                : ""}
            </DialogTitle>
            <DialogDescription>{t("control.mode.confirm.body")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmMode(null)}>
              {t("control.mode.confirm.cancel")}
            </Button>
            <Button
              onClick={() => confirmMode && setModeMut.mutate(confirmMode)}
              disabled={setModeMut.isPending}
            >
              {t("control.mode.confirm.submit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
