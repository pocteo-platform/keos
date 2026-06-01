import { useTranslation } from "react-i18next";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { X } from "lucide-react";
import type { Draft } from "./types";

const TOOLS = [
  "restart_pod",
  "rollout_undo",
  "isolate_pod",
  "apply_network_policy",
  "page_human",
] as const;

interface Props {
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
}

export function Step4Expected({ draft, setDraft }: Props) {
  const { t } = useTranslation();
  const exp = draft.expectation;

  const addKeyword = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = e.currentTarget.value.trim();
    if (!v) return;
    if (!exp.diagnosis_keywords.includes(v)) {
      setDraft({ expectation: { ...exp, diagnosis_keywords: [...exp.diagnosis_keywords, v] } });
    }
    e.currentTarget.value = "";
  };
  const removeKeyword = (k: string) =>
    setDraft({
      expectation: { ...exp, diagnosis_keywords: exp.diagnosis_keywords.filter((x) => x !== k) },
    });

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t("usecases.wizard.expected.description")}</p>

      <div className="space-y-2">
        <Label>{t("usecases.wizard.expected.diagnosisKeywords")}</Label>
        <Input
          placeholder={t("usecases.wizard.expected.keywordPlaceholder")}
          onKeyDown={addKeyword}
        />
        {exp.diagnosis_keywords.length ? (
          <div className="flex flex-wrap gap-2">
            {exp.diagnosis_keywords.map((k) => (
              <Badge key={k} variant="secondary" className="gap-1 pr-1">
                {k}
                <button
                  type="button"
                  onClick={() => removeKeyword(k)}
                  className="rounded hover:text-danger"
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="tool">{t("usecases.wizard.expected.tool")}</Label>
          <Select
            value={exp.proposed_tool ?? "any"}
            onValueChange={(v) =>
              setDraft({ expectation: { ...exp, proposed_tool: v === "any" ? null : v } })
            }
          >
            <SelectTrigger id="tool">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">{t("usecases.wizard.expected.toolAny")}</SelectItem>
              {TOOLS.map((tool) => (
                <SelectItem key={tool} value={tool}>
                  {tool}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label>{t("usecases.wizard.expected.policy")}</Label>
          <ToggleGroup
            type="single"
            value={exp.policy_allow === null ? "any" : String(exp.policy_allow)}
            onValueChange={(v) =>
              v &&
              setDraft({
                expectation: { ...exp, policy_allow: v === "any" ? null : v === "true" },
              })
            }
          >
            <ToggleGroupItem value="any">{t("common.all")}</ToggleGroupItem>
            <ToggleGroupItem value="true">ALLOW</ToggleGroupItem>
            <ToggleGroupItem value="false">DENY</ToggleGroupItem>
          </ToggleGroup>
        </div>

        <div className="space-y-2 lg:col-span-2">
          <Label>{t("usecases.wizard.expected.outcome")}</Label>
          <ToggleGroup
            type="single"
            value={exp.outcome ?? "any"}
            onValueChange={(v) =>
              v &&
              setDraft({
                expectation: {
                  ...exp,
                  outcome: v === "any" ? null : (v as "healed" | "escalated"),
                },
              })
            }
          >
            <ToggleGroupItem value="any">{t("common.all")}</ToggleGroupItem>
            <ToggleGroupItem value="healed">{t("incidents.outcomes.healed")}</ToggleGroupItem>
            <ToggleGroupItem value="escalated">{t("incidents.outcomes.escalated")}</ToggleGroupItem>
          </ToggleGroup>
        </div>
      </div>
    </div>
  );
}

export const isStep4Valid = (_: Draft) => true; // expectations are optional
