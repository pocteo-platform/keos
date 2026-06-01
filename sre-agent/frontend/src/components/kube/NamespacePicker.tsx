import { useTranslation } from "react-i18next";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

export function NamespacePicker({
  value,
  onChange,
  namespaces,
}: {
  value: string;
  onChange: (ns: string) => void;
  namespaces: string[];
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3">
      <Label htmlFor="ns-picker">{t("cluster.namespacePicker")}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id="ns-picker" className="w-56">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {namespaces.map((ns) => (
            <SelectItem key={ns} value={ns}>
              {ns}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
