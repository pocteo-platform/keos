import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { JsonBlock } from "@/components/ui/json-block";
import type { IncidentDetail } from "@/lib/api";

export function ActionResultTab({ incident }: { incident: IncidentDetail }) {
  const { t } = useTranslation();
  const ar = (incident.action_result ?? {}) as {
    tool?: string;
    args?: Record<string, unknown>;
    dry_run?: boolean;
    success?: boolean;
    stdout?: string;
    stderr?: string;
    error?: string | null;
  };

  if (!incident.action_result) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          {t("incidents.tabs.action.empty")}
        </CardContent>
      </Card>
    );
  }

  const status: { variant: "ok" | "warn" | "danger"; label: string } = ar.dry_run
    ? { variant: "warn", label: "DRY-RUN" }
    : ar.success
      ? { variant: "ok", label: "OK" }
      : { variant: "danger", label: "FAIL" };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Badge variant="secondary">{ar.tool ?? "?"}</Badge>
            <Badge variant={status.variant}>{status.label}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {ar.stdout ? (
            <Block label={t("incidents.tabs.action.stdout")} body={ar.stdout} tone="muted" />
          ) : null}
          {ar.stderr ? (
            <Block label={t("incidents.tabs.action.stderr")} body={ar.stderr} tone="warn" />
          ) : null}
          {ar.error ? (
            <Block label={t("incidents.tabs.action.error")} body={ar.error} tone="danger" />
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t("incidents.tabs.action.raw")}</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonBlock data={incident.action_result ?? {}} maxHeightRem={20} />
        </CardContent>
      </Card>
    </div>
  );
}

function Block({
  label,
  body,
  tone,
}: {
  label: string;
  body: string;
  tone: "muted" | "warn" | "danger";
}) {
  const klass =
    tone === "danger"
      ? "border-danger/40 bg-danger/10"
      : tone === "warn"
        ? "border-warn/40 bg-warn/10"
        : "bg-muted/30";
  return (
    <div>
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <pre className={`mt-1 whitespace-pre-wrap rounded-md border p-3 text-xs ${klass}`}>
        {body}
      </pre>
    </div>
  );
}
