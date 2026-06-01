import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import { useChannel } from "@/lib/ws";
import { Activity } from "lucide-react";

interface FalcoEvent {
  rule?: string;
  output?: string;
  incident_id?: string;
  output_fields?: { "k8s.ns.name"?: string; "k8s.pod.name"?: string };
}

// Slides down on every Falco event the agent observes; clickable to jump
// to /pipeline/live; auto-dismisses after 8 seconds.
export function LiveTailBanner() {
  const [current, setCurrent] = useState<FalcoEvent | null>(null);
  const navigate = useNavigate();

  useChannel("falco", (event) => {
    setCurrent(event as FalcoEvent);
  });

  useEffect(() => {
    if (!current) return;
    const t = window.setTimeout(() => setCurrent(null), 8000);
    return () => window.clearTimeout(t);
  }, [current]);

  return (
    <AnimatePresence>
      {current ? (
        <motion.button
          type="button"
          initial={{ y: -50, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: -50, opacity: 0 }}
          transition={{ duration: 0.25 }}
          onClick={() => navigate(`/pipeline/live`)}
          className="absolute left-1/2 top-2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full border bg-card px-4 py-2 text-sm shadow-md hover:bg-accent"
        >
          <Activity className="h-4 w-4 text-info" />
          <span className="font-medium">{current.rule ?? "Falco event"}</span>
          <span className="text-muted-foreground">
            {current.output_fields?.["k8s.ns.name"]} / {current.output_fields?.["k8s.pod.name"]}
          </span>
        </motion.button>
      ) : null}
    </AnimatePresence>
  );
}
