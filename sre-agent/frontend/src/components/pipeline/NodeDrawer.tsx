import { useTranslation } from "react-i18next";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import type { NodeKind } from "@/lib/pipeline-topology";
import type { NodeState } from "@/hooks/usePipelineState";

interface Props {
  kind: NodeKind | null;
  state: NodeState | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function NodeDrawer({ kind, state, open, onOpenChange }: Props) {
  const { t } = useTranslation();
  if (!kind || !state) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" className="overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {t(`pipeline.nodes.${kind}.title`)}
            <Badge
              variant={
                state.status === "done"
                  ? "ok"
                  : state.status === "error"
                    ? "danger"
                    : state.status === "active"
                      ? "info"
                      : "secondary"
              }
            >
              {state.status}
            </Badge>
            {state.elapsedMs != null ? (
              <span className="ml-auto font-mono text-xs text-muted-foreground">
                {state.elapsedMs}ms
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>{t(`pipeline.nodes.${kind}.desc`)}</DialogDescription>
        </DialogHeader>

        <section className="mt-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("pipeline.drawer.input")}</h3>
          {state.input ? (
            <pre className="max-h-64 overflow-auto rounded-md border bg-muted/30 p-3 text-[11px] leading-snug">
              {JSON.stringify(state.input, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground">{t("common.none")}</p>
          )}
        </section>

        <section className="mt-4 space-y-2">
          <h3 className="text-sm font-semibold">{t("pipeline.drawer.output")}</h3>
          {state.output ? (
            <pre className="max-h-80 overflow-auto rounded-md border bg-muted/30 p-3 text-[11px] leading-snug">
              {JSON.stringify(state.output, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground">{t("common.none")}</p>
          )}
        </section>

        {state.error ? (
          <section className="mt-4">
            <h3 className="text-sm font-semibold text-danger">{t("pipeline.drawer.error")}</h3>
            <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-danger/40 bg-danger/10 p-3 text-[11px]">
              {state.error}
            </pre>
          </section>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
