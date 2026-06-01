import { useTranslation } from "react-i18next";
import { Check, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export function HmacChip({ valid }: { valid: boolean }) {
  const { t } = useTranslation();
  return valid ? (
    <Badge variant="ok" className="gap-1">
      <Check className="h-3 w-3" />
      {t("control.audit.valid")}
    </Badge>
  ) : (
    <Badge variant="danger" className="gap-1">
      <X className="h-3 w-3" />
      {t("control.audit.invalid")}
    </Badge>
  );
}
