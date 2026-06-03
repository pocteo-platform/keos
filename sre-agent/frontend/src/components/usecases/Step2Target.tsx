import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { Draft } from "./types";

const OWNER_KINDS = ["Deployment", "StatefulSet", "DaemonSet", "Standalone"] as const;
type OwnerKind = (typeof OWNER_KINDS)[number];
type Mode = "specific" | "label";

interface Props {
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
}

export function Step2Target({ draft, setDraft }: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode>(draft.target.pod_name ? "specific" : "label");
  const [keyDraft, setKeyDraft] = useState("");
  const [valDraft, setValDraft] = useState("");

  const namespacesQ = useQuery({
    queryKey: ["namespaces"],
    queryFn: api.listNamespaces,
  });
  const podsQ = useQuery({
    queryKey: ["pods", draft.target.namespace],
    queryFn: () => api.listPods(draft.target.namespace),
    enabled: Boolean(draft.target.namespace),
  });

  const selector = draft.target.pod_label_selector ?? {};

  const setLabel = (k: string, v: string) => {
    const next = { ...selector, [k]: v };
    setDraft({ target: { ...draft.target, pod_label_selector: next, pod_name: null } });
  };
  const removeLabel = (k: string) => {
    const next = { ...selector };
    delete next[k];
    setDraft({ target: { ...draft.target, pod_label_selector: next } });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="ns">{t("usecases.wizard.target.namespace")}</Label>
          <Select
            value={draft.target.namespace}
            onValueChange={(v) => setDraft({ target: { ...draft.target, namespace: v } })}
          >
            <SelectTrigger id="ns">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(namespacesQ.data ?? []).map((n) => (
                <SelectItem key={n.name} value={n.name}>
                  {n.name}
                </SelectItem>
              ))}
              {(namespacesQ.data ?? []).length === 0 ? (
                <SelectItem value={draft.target.namespace}>{draft.target.namespace}</SelectItem>
              ) : null}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="owner">{t("usecases.wizard.target.ownerKind")}</Label>
          <Select
            value={draft.target.owner_kind}
            onValueChange={(v) =>
              setDraft({ target: { ...draft.target, owner_kind: v as OwnerKind } })
            }
          >
            <SelectTrigger id="owner">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OWNER_KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div>
        <Label>{t("usecases.wizard.target.podMode")}</Label>
        <ToggleGroup
          type="single"
          value={mode}
          onValueChange={(v) => v && setMode(v as Mode)}
          className="mt-2"
        >
          <ToggleGroupItem value="specific">
            {t("usecases.wizard.target.modeSpecific")}
          </ToggleGroupItem>
          <ToggleGroupItem value="label">{t("usecases.wizard.target.modeLabel")}</ToggleGroupItem>
        </ToggleGroup>
      </div>

      {mode === "specific" ? (
        <div className="space-y-2">
          <Label htmlFor="pod">{t("usecases.wizard.target.podName")}</Label>
          <Select
            value={draft.target.pod_name ?? ""}
            onValueChange={(v) =>
              setDraft({ target: { ...draft.target, pod_name: v, pod_label_selector: null } })
            }
          >
            <SelectTrigger id="pod">
              <SelectValue placeholder={t("usecases.wizard.target.pickPod")} />
            </SelectTrigger>
            <SelectContent>
              {(podsQ.data ?? []).map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="space-y-2">
          <Label>{t("usecases.wizard.target.podLabels")}</Label>
          {Object.entries(selector).length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("usecases.wizard.target.noLabels")}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {Object.entries(selector).map(([k, v]) => (
                <span
                  key={k}
                  className="inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 font-mono text-xs"
                >
                  {k}={v}
                  <button
                    type="button"
                    onClick={() => removeLabel(k)}
                    className="ml-1 rounded hover:text-danger"
                    aria-label={`remove ${k}`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Input
              placeholder="key"
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              className="max-w-40"
            />
            <Input
              placeholder="value"
              value={valDraft}
              onChange={(e) => setValDraft(e.target.value)}
              className="max-w-40"
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                if (!keyDraft.trim()) return;
                setLabel(keyDraft.trim(), valDraft.trim());
                setKeyDraft("");
                setValDraft("");
              }}
            >
              {t("usecases.wizard.target.addLabel")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// Validator moved to `./step-validators.ts`.
