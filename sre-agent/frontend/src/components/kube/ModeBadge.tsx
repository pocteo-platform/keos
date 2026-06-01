import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { AgentMode } from "@/lib/api";

const variantByMode: Record<AgentMode, "ok" | "warn" | "danger"> = {
  active: "ok",
  "observe-only": "warn",
  frozen: "danger",
};

export function ModeBadge({ mode }: { mode: AgentMode | null | undefined }) {
  const { t } = useTranslation();
  if (!mode) {
    return <Badge variant="outline">{t("common.none")}</Badge>;
  }
  return <Badge variant={variantByMode[mode]}>{t(`modes.${mode}`)}</Badge>;
}
