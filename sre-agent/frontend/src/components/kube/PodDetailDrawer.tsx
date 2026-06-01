import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { relativeTimeSeconds } from "@/lib/utils";
import type { PodSummary } from "@/lib/api";

interface Props {
  pod: PodSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function PodDetailDrawer({ pod, open, onOpenChange }: Props) {
  const { t } = useTranslation();
  if (!pod) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" className="overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-mono text-base">{pod.name}</DialogTitle>
          <DialogDescription>
            {pod.namespace} ·{" "}
            {pod.owner_kind ? `${pod.owner_kind}/${pod.owner_name}` : t("cluster.podCard.noOwner")}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-6 grid grid-cols-2 gap-4 text-sm">
          <Field label={t("cluster.podCard.phase")} value={<Badge>{pod.phase}</Badge>} />
          <Field label={t("cluster.podCard.ready")} value={pod.ready} />
          <Field label={t("cluster.podCard.restarts")} value={pod.restarts} />
          <Field label={t("cluster.podCard.age")} value={relativeTimeSeconds(pod.age_seconds)} />
        </div>

        {pod.last_event ? (
          <section className="mt-6">
            <h3 className="text-sm font-semibold">{t("cluster.drawer.events")}</h3>
            <pre className="mt-2 whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-xs">
              {pod.last_event}
            </pre>
          </section>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border bg-card/30 p-3">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="mt-1">{value}</div>
    </div>
  );
}
