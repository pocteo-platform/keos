import { useState } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  data: unknown;
  className?: string;
  /** Max height in rem; pre wrapper scrolls beyond that. Defaults to 24rem. */
  maxHeightRem?: number;
  /** Show a copy-to-clipboard button in the top-right. Defaults to true. */
  copyable?: boolean;
}

/** Shared, lightweight JSON viewer. Doesn't pull in a syntax highlighter to
 *  keep the bundle small; the audience is technical and a `<pre>` is fine. */
export function JsonBlock({ data, className, maxHeightRem = 24, copyable = true }: Props) {
  const [copied, setCopied] = useState(false);
  const text = JSON.stringify(data, null, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className={cn("relative", className)}>
      {copyable ? (
        <button
          type="button"
          onClick={copy}
          className="absolute right-2 top-2 z-10 rounded-md border bg-card/80 p-1.5 text-muted-foreground backdrop-blur transition hover:text-foreground"
          aria-label="Copy"
        >
          {copied ? <Check className="h-3 w-3 text-ok" /> : <Copy className="h-3 w-3" />}
        </button>
      ) : null}
      <pre
        className="overflow-auto rounded-md border bg-muted/30 p-3 text-[11px] leading-snug"
        style={{ maxHeight: `${maxHeightRem}rem` }}
      >
        {text}
      </pre>
    </div>
  );
}
