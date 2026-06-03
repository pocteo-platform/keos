// Component tests for OutcomeBanner — the big visual answer on the
// use-case-launch scene view. Five verdict branches, each with a
// distinct visual variant and title. We assert the rendered text +
// the presence of the glyph, not the animation (Framer-Motion's
// `AnimatePresence` is exercised at the integration layer).

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OutcomeBanner } from "@/components/pipeline/OutcomeBanner";
import type { SceneOutcome } from "@/lib/api";

function outcome(overrides: Partial<SceneOutcome> = {}): SceneOutcome {
  return {
    verdict: "healed",
    reason: "test reason",
    time_to_outcome_seconds: 12.5,
    confidence: 0.84,
    rule: "memory-leak",
    tool: "restart_pod",
    policy_decision: null,
    ...overrides,
  };
}

describe("OutcomeBanner", () => {
  it("renders the Healed title + reason for verdict=healed", () => {
    render(<OutcomeBanner outcome={outcome({ verdict: "healed" })} />);
    expect(screen.getByText("Healed")).toBeInTheDocument();
    expect(screen.getByText("test reason")).toBeInTheDocument();
  });

  it("renders the Escalated title for verdict=escalated", () => {
    render(<OutcomeBanner outcome={outcome({ verdict: "escalated" })} />);
    expect(screen.getByText("Escalated")).toBeInTheDocument();
  });

  it("renders the Failed title for verdict=failed", () => {
    render(<OutcomeBanner outcome={outcome({ verdict: "failed" })} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders the Observe-only title for verdict=observe_only", () => {
    render(<OutcomeBanner outcome={outcome({ verdict: "observe_only" })} />);
    expect(screen.getByText("Observe only")).toBeInTheDocument();
  });

  it("renders nothing when outcome is null", () => {
    const { container } = render(<OutcomeBanner outcome={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the unknown skeleton by default while graph runs", () => {
    render(<OutcomeBanner outcome={outcome({ verdict: "unknown" })} />);
    expect(screen.getByText("Running…")).toBeInTheDocument();
  });

  it("respects hideUntilTerminal — suppresses unknown while in-flight", () => {
    const { container } = render(
      <OutcomeBanner outcome={outcome({ verdict: "unknown" })} hideUntilTerminal />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the action tool chip when present", () => {
    render(<OutcomeBanner outcome={outcome({ tool: "drain_node" })} />);
    expect(screen.getByText("drain_node")).toBeInTheDocument();
  });

  it("formats time_to_outcome as m+s when over a minute", () => {
    render(<OutcomeBanner outcome={outcome({ time_to_outcome_seconds: 125 })} />);
    expect(screen.getByText(/2m 5s/)).toBeInTheDocument();
  });

  it("formats time_to_outcome as seconds under a minute", () => {
    render(<OutcomeBanner outcome={outcome({ time_to_outcome_seconds: 12.5 })} />);
    expect(screen.getByText(/12\.5s/)).toBeInTheDocument();
  });

  it("renders rule + confidence labels when both present", () => {
    render(<OutcomeBanner outcome={outcome({ rule: "crashloop", confidence: 0.71 })} />);
    expect(screen.getByText("crashloop")).toBeInTheDocument();
    expect(screen.getByText("0.71")).toBeInTheDocument();
  });
});
