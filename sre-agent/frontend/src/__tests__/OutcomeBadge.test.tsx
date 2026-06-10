// Component tests for OutcomeBadge — the chip in the Incidents table
// that tells an operator at a glance whether the agent fixed it, escalated,
// auto-resolved (stale), or hit a real failure. The precedence between
// these four states is non-obvious enough to warrant guard-rail tests.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OutcomeBadge } from "@/components/incidents/OutcomeBadge";
import type { IncidentSummary } from "@/lib/api";

// Helper: build an IncidentSummary with sensible defaults, override the
// fields under test.
function makeIncident(overrides: Partial<IncidentSummary> = {}): IncidentSummary {
  return {
    incident_id: "inc-test",
    ts: "2026-06-01T00:00:00Z",
    rule: "test rule",
    severity: "medium",
    namespace: "workloads",
    pod: "test-pod",
    diagnosis_summary: null,
    proposed_tool: null,
    policy_allow: null,
    healed: null,
    escalated: false,
    agent_mode: "active",
    domain: "sre",
    stale_resolved: false,
    ...overrides,
  };
}

describe("OutcomeBadge", () => {
  it("renders 'Auto-resolved' when stale_resolved is true (highest priority)", () => {
    // stale_resolved must win even if escalated=true — they're often both
    // true (we go through escalate to skip page_human, but the OUTCOME is
    // auto-resolved, not escalated-for-human-action).
    render(<OutcomeBadge incident={makeIncident({ stale_resolved: true, escalated: true })} />);
    expect(screen.getByText("Auto-resolved")).toBeInTheDocument();
  });

  it("renders 'Escalated' when escalated=true and not stale", () => {
    render(<OutcomeBadge incident={makeIncident({ escalated: true })} />);
    expect(screen.getByText("incidents.outcomes.escalated")).toBeInTheDocument();
  });

  it("renders 'Healed' when healed=true", () => {
    render(<OutcomeBadge incident={makeIncident({ healed: true })} />);
    expect(screen.getByText("incidents.outcomes.healed")).toBeInTheDocument();
  });

  it("renders 'Failed' when healed=false (verify returned not-healed)", () => {
    render(<OutcomeBadge incident={makeIncident({ healed: false })} />);
    expect(screen.getByText("incidents.outcomes.failed")).toBeInTheDocument();
  });

  it("renders 'Unknown' when nothing terminal has happened (in-flight)", () => {
    render(<OutcomeBadge incident={makeIncident()} />);
    expect(screen.getByText("incidents.outcomes.unknown")).toBeInTheDocument();
  });
});
