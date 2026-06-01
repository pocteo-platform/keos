import { useTranslation } from "react-i18next";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { X } from "lucide-react";
import type { Draft } from "./types";

interface Props {
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
}

export function Step1Context({ draft, setDraft }: Props) {
  const { t } = useTranslation();

  const addTag = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = e.currentTarget.value.trim();
    if (!v) return;
    if (!draft.tags.includes(v)) setDraft({ tags: [...draft.tags, v] });
    e.currentTarget.value = "";
  };

  const removeTag = (t: string) => setDraft({ tags: draft.tags.filter((x) => x !== t) });

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="name">
          {t("usecases.wizard.context.name")} <span className="text-danger">*</span>
        </Label>
        <Input
          id="name"
          value={draft.name}
          onChange={(e) => setDraft({ name: e.target.value })}
          placeholder={t("usecases.wizard.context.namePlaceholder")}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="desc-en">{t("usecases.wizard.context.descriptionEn")}</Label>
          <Textarea
            id="desc-en"
            rows={4}
            value={draft.description_en}
            onChange={(e) => setDraft({ description_en: e.target.value })}
            placeholder={t("usecases.wizard.context.descriptionEnPlaceholder")}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="desc-fr">{t("usecases.wizard.context.descriptionFr")}</Label>
          <Textarea
            id="desc-fr"
            rows={4}
            value={draft.description_fr}
            onChange={(e) => setDraft({ description_fr: e.target.value })}
            placeholder={t("usecases.wizard.context.descriptionFrPlaceholder")}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>{t("usecases.wizard.context.tags")}</Label>
        <Input
          placeholder={t("usecases.wizard.context.tagsPlaceholder")}
          onKeyDown={addTag}
          defaultValue=""
        />
        {draft.tags.length ? (
          <div className="flex flex-wrap gap-2">
            {draft.tags.map((tag) => (
              <Badge key={tag} variant="secondary" className="gap-1 pr-1">
                {tag}
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  className="rounded-sm hover:text-danger"
                  aria-label={`remove ${tag}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export const isStep1Valid = (d: Draft) =>
  d.name.trim().length > 0 &&
  (d.description_en.trim().length > 0 || d.description_fr.trim().length > 0);
