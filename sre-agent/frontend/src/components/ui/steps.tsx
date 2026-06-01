import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  steps: { id: number; label: string }[];
  current: number;
  onStepClick?: (id: number) => void;
}

/** Horizontal step indicator. Active step is highlighted, completed steps
 *  get a check icon and are clickable to jump back. Forward jumps are
 *  disabled — the wizard validates step-by-step. */
export function Steps({ steps, current, onStepClick }: Props) {
  return (
    // Horizontal scroll on narrow screens — the 6 step labels don't fit on a
    // phone. Each step keeps a min-width so labels stay readable; the
    // container scrolls instead of crushing them.
    <ol className="flex w-full items-center gap-1 overflow-x-auto">
      {steps.map((s, idx) => {
        const done = s.id < current;
        const active = s.id === current;
        const disabled = s.id > current;
        return (
          <li key={s.id} className="flex flex-1 shrink-0 items-center">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onStepClick?.(s.id)}
              className={cn(
                "flex min-w-[7rem] flex-1 items-center gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors sm:px-3",
                active && "bg-primary/10 text-primary",
                done && "text-ok hover:bg-accent",
                !active && !done && "text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-bold",
                  active && "border-primary bg-primary text-primary-foreground",
                  done && "border-ok bg-ok text-background",
                  !active && !done && "border-border",
                )}
              >
                {done ? <Check className="h-3 w-3" /> : s.id}
              </span>
              {/* Label hidden on smallest screens — the number + active-state colour
                  carries enough context. Re-appears at sm+ where there's room. */}
              <span className="hidden truncate font-medium sm:inline">{s.label}</span>
            </button>
            {idx < steps.length - 1 ? (
              <span className={cn("h-px w-2 shrink-0 sm:w-4", done ? "bg-ok" : "bg-border")} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
