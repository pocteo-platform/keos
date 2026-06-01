import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonBlock } from "@/components/ui/json-block";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { X } from "lucide-react";
import type { Draft } from "./types";

const PRIORITIES = ["Notice", "Warning", "Error", "Critical"] as const;
type Priority = (typeof PRIORITIES)[number];

// Chaos Mesh experiment kinds. "none" means a synthetic-only use case
// (NATS publish without applying a CR). The dropdown writes into
// `draft.falco.output_fields["chaos.kind"]`, which the backend's
// usecase_runner reads at run time.
const CHAOS_KINDS = [
  { id: "none", label: "None (synthetic event only)" },
  { id: "PodChaos", label: "PodChaos (pod-kill / pod-failure)" },
  { id: "StressChaos", label: "StressChaos (CPU / memory stress)" },
  { id: "NetworkChaos", label: "NetworkChaos (delay / loss)" },
  { id: "IOChaos", label: "IOChaos (filesystem latency)" },
] as const;

interface Props {
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
}

/** Combined telemetry + chaos step. Carries both:
 *   - The Falco-shaped event template (rule / priority / output / fields).
 *   - An optional Chaos Mesh experiment that the use-case runner applies
 *     to the cluster before publishing the event. */
export function Step3FalcoTemplate({ draft, setDraft }: Props) {
  const { t } = useTranslation();
  const [keyDraft, setKeyDraft] = useState("");
  const [valDraft, setValDraft] = useState("");

  const previewPod =
    draft.target.pod_name ||
    Object.values(draft.target.pod_label_selector ?? {}).join("-") ||
    "example-pod-abc";
  const previewNs = draft.target.namespace || "workloads";

  const renderedOutput = useMemo(() => {
    let out = draft.falco.output_template;
    out = out.replace(/\{pod\}/g, previewPod);
    out = out.replace(/\{ns\}/g, previewNs);
    out = out.replace(/\{image\}/g, draft.falco.output_fields.image ?? "");
    return out;
  }, [draft.falco.output_template, draft.falco.output_fields, previewPod, previewNs]);

  const previewEvent = useMemo(
    () => ({
      rule: draft.falco.rule,
      priority: draft.falco.priority,
      source: "syscall",
      output: renderedOutput,
      output_fields: {
        "k8s.ns.name": previewNs,
        "k8s.pod.name": previewPod,
        ...draft.falco.output_fields,
      },
    }),
    [draft.falco, renderedOutput, previewNs, previewPod],
  );

  const setField = (k: string, v: string) => {
    const next = { ...draft.falco.output_fields, [k]: v };
    setDraft({ falco: { ...draft.falco, output_fields: next } });
  };
  const removeField = (k: string) => {
    const next = { ...draft.falco.output_fields };
    delete next[k];
    setDraft({ falco: { ...draft.falco, output_fields: next } });
  };

  const chaosKind = draft.falco.output_fields["chaos.kind"] ?? "none";
  const setChaosField = (k: string, v: string) => {
    const next = { ...draft.falco.output_fields, [k]: v };
    if (k === "chaos.kind" && v === "none") {
      // Strip all chaos.* fields when switching back to synthetic-only.
      for (const key of Object.keys(next)) {
        if (key.startsWith("chaos.")) delete next[key];
      }
    }
    setDraft({ falco: { ...draft.falco, output_fields: next } });
  };

  return (
    <div className="space-y-6">
      {/* Chaos Mesh picker (optional). When set to anything but "none",
          usecase_runner will apply a corresponding ChaosExperiment CR
          before publishing the synthetic event. */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            {t("usecases.wizard.chaos.title")}
            <Badge variant={chaosKind === "none" ? "secondary" : "info"}>{chaosKind}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div>
            <Label htmlFor="chaos-kind">{t("usecases.wizard.chaos.kind")}</Label>
            <Select value={chaosKind} onValueChange={(v) => setChaosField("chaos.kind", v)}>
              <SelectTrigger id="chaos-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CHAOS_KINDS.map((k) => (
                  <SelectItem key={k.id} value={k.id}>
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {chaosKind !== "none" ? (
            <>
              <div>
                <Label htmlFor="chaos-action">{t("usecases.wizard.chaos.action")}</Label>
                <Input
                  id="chaos-action"
                  placeholder="pod-kill / delay / latency"
                  value={draft.falco.output_fields["chaos.action"] ?? ""}
                  onChange={(e) => setChaosField("chaos.action", e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="chaos-duration">{t("usecases.wizard.chaos.duration")}</Label>
                <Input
                  id="chaos-duration"
                  placeholder="30s"
                  value={draft.falco.output_fields["chaos.duration"] ?? "30s"}
                  onChange={(e) => setChaosField("chaos.duration", e.target.value)}
                />
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rule">
              {t("usecases.wizard.falco.rule")} <span className="text-danger">*</span>
            </Label>
            <Input
              id="rule"
              value={draft.falco.rule}
              onChange={(e) => setDraft({ falco: { ...draft.falco, rule: e.target.value } })}
              placeholder={t("usecases.wizard.falco.rulePlaceholder")}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="priority">{t("usecases.wizard.falco.priority")}</Label>
            <Select
              value={draft.falco.priority}
              onValueChange={(v) =>
                setDraft({ falco: { ...draft.falco, priority: v as Priority } })
              }
            >
              <SelectTrigger id="priority">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIORITIES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="output">{t("usecases.wizard.falco.outputTemplate")}</Label>
            <Textarea
              id="output"
              rows={3}
              value={draft.falco.output_template}
              onChange={(e) =>
                setDraft({ falco: { ...draft.falco, output_template: e.target.value } })
              }
              placeholder={t("usecases.wizard.falco.outputPlaceholder")}
            />
            <p className="text-xs text-muted-foreground">
              {t("usecases.wizard.falco.placeholdersHint")}
            </p>
          </div>

          <div className="space-y-2">
            <Label>{t("usecases.wizard.falco.fields")}</Label>
            {Object.entries(draft.falco.output_fields).length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("common.none")}</p>
            ) : (
              <ul className="space-y-1">
                {Object.entries(draft.falco.output_fields).map(([k, v]) => (
                  <li
                    key={k}
                    className="flex items-center gap-2 rounded-md border bg-card px-2 py-1 font-mono text-xs"
                  >
                    <span className="font-semibold">{k}</span>
                    <span className="text-muted-foreground">=</span>
                    <span className="flex-1 truncate">{v}</span>
                    <button
                      type="button"
                      onClick={() => removeField(k)}
                      className="rounded hover:text-danger"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <Input
                placeholder="proc.cmdline"
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                className="max-w-44"
              />
              <Input
                placeholder="/bin/sh"
                value={valDraft}
                onChange={(e) => setValDraft(e.target.value)}
                className="flex-1"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  if (!keyDraft.trim()) return;
                  setField(keyDraft.trim(), valDraft);
                  setKeyDraft("");
                  setValDraft("");
                }}
              >
                {t("common.save")}
              </Button>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Label>{t("usecases.wizard.falco.preview")}</Label>
          <JsonBlock data={previewEvent} maxHeightRem={28} />
        </div>
      </div>
    </div>
  );
}

export const isStep3Valid = (d: Draft) =>
  d.falco.rule.trim().length > 0 && d.falco.output_template.trim().length > 0;
