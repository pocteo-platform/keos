import { useTranslation } from "react-i18next";
import { Pause, Play, SkipForward, FastForward } from "lucide-react";
import { Button } from "@/components/ui/button";

type Speed = 0.5 | 1 | 2 | 4 | "instant";

interface Props {
  totalMs: number;
  cursorMs: number;
  playing: boolean;
  speed: Speed;
  onSeek: (ms: number) => void;
  onTogglePlay: () => void;
  onStep: () => void;
  onSpeed: (s: Speed) => void;
}

const SPEEDS: Speed[] = [0.5, 1, 2, 4, "instant"];

export function Scrubber({
  totalMs,
  cursorMs,
  playing,
  speed,
  onSeek,
  onTogglePlay,
  onStep,
  onSpeed,
}: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-2 rounded-md border bg-card p-3">
      <div className="flex items-center gap-3">
        <Button
          size="icon"
          variant="outline"
          onClick={onTogglePlay}
          aria-label={playing ? t("common.cancel") : t("common.run")}
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>
        <Button
          size="icon"
          variant="outline"
          onClick={onStep}
          aria-label={t("pipeline.scrubber.step")}
        >
          <SkipForward className="h-4 w-4" />
        </Button>
        <input
          type="range"
          min={0}
          max={Math.max(totalMs, 1)}
          step={20}
          value={Math.min(cursorMs, totalMs)}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="flex-1 cursor-pointer accent-primary"
          aria-label={t("pipeline.scrubber.scrub")}
        />
        <span className="w-20 text-right font-mono text-xs text-muted-foreground">
          {formatMs(cursorMs)} / {formatMs(totalMs)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <FastForward className="h-3.5 w-3.5 text-muted-foreground" />
        <div className="flex gap-1">
          {SPEEDS.map((s) => (
            <Button
              key={String(s)}
              size="sm"
              variant={s === speed ? "default" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => onSpeed(s)}
            >
              {s === "instant" ? t("pipeline.scrubber.instant") : `${s}×`}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}
