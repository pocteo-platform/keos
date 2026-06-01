import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";

// Placeholder used by routes that arrive in subsequent subphases (8.3-8.5).
// Renders a single empty-state message so the navigation always has somewhere
// to land. No mock data.
export function StubPage({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{t(titleKey)}</h1>
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          {t("common.empty")}
        </CardContent>
      </Card>
    </div>
  );
}
