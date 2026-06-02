/**
 * Calibration view — Phase 12.
 *
 * Tracks the agent's confidence per rule, the gating threshold each rule
 * is currently subject to (override / learned / global), and the accuracy
 * the agent has earned on that rule. Operators use this page to:
 *
 *   • check whether the cluster is on track for ≥ 90 % median confidence;
 *   • pin a permissive threshold on a rule the operator trusts;
 *   • pin a stricter threshold on a rule that's been wrong recently.
 *
 * The data comes from /api/calibration; the operator-pinned threshold goes
 * back through /api/calibration/<rule>/threshold and is persisted by the
 * SQLite store inside the agent.
 */

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type CalibrationRule } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ShieldCheck, TrendingUp, AlertTriangle } from "lucide-react";

const NINETY_PCT_TARGET = 0.9;

function pct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return x.toFixed(digits);
}

function thresholdSourceLabel(rule: CalibrationRule): string {
  if (rule.threshold_override !== null) return "operator override";
  if (rule.n_graded >= 20) return "learned (p25)";
  return "global default";
}

export default function CalibrationPage() {
  const cal = useQuery({
    queryKey: ["calibration"],
    queryFn: api.listCalibration,
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Calibration</h1>
          <p className="text-sm text-muted-foreground">
            Per-rule diagnostic confidence + gating thresholds. Target:{" "}
            <strong className="text-foreground">≥ 90 % median</strong> across all rules.
          </p>
        </div>
        <Badge variant="info" className="gap-1">
          <ShieldCheck className="h-3 w-3" />
          phase 12
        </Badge>
      </header>

      <ClusterSummary
        loading={cal.isLoading}
        median={cal.data?.median_confidence ?? null}
        pctAbove90={cal.data?.pct_above_90 ?? null}
        ruleCount={cal.data?.total ?? 0}
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Rules</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {cal.isLoading ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">Loading…</p>
          ) : cal.data && cal.data.items.length > 0 ? (
            <RulesTable items={cal.data.items} />
          ) : (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              No rules graded yet. Run a few scenarios to populate this view.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ClusterSummary({
  loading,
  median,
  pctAbove90,
  ruleCount,
}: {
  loading: boolean;
  median: number | null;
  pctAbove90: number | null;
  ruleCount: number;
}) {
  const onTarget = median !== null && median >= NINETY_PCT_TARGET;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <Card>
        <CardContent className="flex items-center justify-between p-4">
          <div>
            <p className="text-xs uppercase tracking-wider text-muted-foreground">
              median confidence
            </p>
            <p className="mt-1 text-2xl font-bold">
              {loading ? "…" : num(median)}
            </p>
          </div>
          {onTarget ? (
            <TrendingUp className="h-6 w-6 text-success" />
          ) : (
            <AlertTriangle className="h-6 w-6 text-warning" />
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-4">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            rules ≥ 90 %
          </p>
          <p className="mt-1 text-2xl font-bold">{loading ? "…" : pct(pctAbove90)}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            target: 100 % of rules at ≥ 90 % median
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-4">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            rules tracked
          </p>
          <p className="mt-1 text-2xl font-bold">{loading ? "…" : ruleCount}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            grows as new event kinds are seen
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function RulesTable({ items }: { items: CalibrationRule[] }) {
  // Sort: worst accuracy first (so problems surface at the top), then by rule_id.
  const sorted = [...items].sort((a, b) => {
    const aa = a.accuracy ?? 2; // sentinel so "no data" rules sink to bottom
    const bb = b.accuracy ?? 2;
    if (aa !== bb) return aa - bb;
    return a.rule_id.localeCompare(b.rule_id);
  });
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b text-left text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2">Rule</th>
            <th className="px-4 py-2 text-right">Total</th>
            <th className="px-4 py-2 text-right">Graded</th>
            <th className="px-4 py-2 text-right">Accuracy</th>
            <th className="px-4 py-2 text-right">p50</th>
            <th className="px-4 py-2 text-right">p90</th>
            <th className="px-4 py-2 text-right">Threshold</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <RuleRow key={r.rule_id} rule={r} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RuleRow({ rule }: { rule: CalibrationRule }) {
  const accuracy = rule.accuracy;
  const accColor =
    accuracy === null
      ? "text-muted-foreground"
      : accuracy >= 0.9
        ? "text-success"
        : accuracy >= 0.75
          ? "text-foreground"
          : "text-danger";

  return (
    <tr className="border-b last:border-0 hover:bg-accent/30">
      <td className="px-4 py-3 align-top">
        <div className="font-mono text-xs">{rule.rule_id}</div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          {thresholdSourceLabel(rule)}
        </div>
      </td>
      <td className="px-4 py-3 text-right align-top tabular-nums">{rule.n_total}</td>
      <td className="px-4 py-3 text-right align-top tabular-nums">{rule.n_graded}</td>
      <td className={`px-4 py-3 text-right align-top tabular-nums ${accColor}`}>
        {pct(accuracy)}
      </td>
      <td className="px-4 py-3 text-right align-top tabular-nums">
        {num(rule.p50_confidence)}
      </td>
      <td className="px-4 py-3 text-right align-top tabular-nums">
        {num(rule.p90_confidence)}
      </td>
      <td className="px-4 py-3 text-right align-top tabular-nums font-medium">
        {num(rule.effective_threshold)}
      </td>
      <td className="px-4 py-3 text-right align-top">
        <ThresholdOverride rule={rule} />
      </td>
    </tr>
  );
}

function ThresholdOverride({ rule }: { rule: CalibrationRule }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<string>(
    rule.threshold_override !== null ? String(rule.threshold_override) : "",
  );

  const setMutation = useMutation({
    mutationFn: (threshold: number | null) =>
      api.setCalibrationThreshold(rule.rule_id, threshold),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calibration"] });
      setEditing(false);
    },
  });

  if (!editing) {
    return (
      <div className="flex items-center justify-end gap-2">
        {rule.threshold_override !== null ? (
          <Badge variant="warn" className="text-[10px]">
            pinned {num(rule.threshold_override)}
          </Badge>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => setEditing(true)}
        >
          {rule.threshold_override !== null ? "Edit" : "Pin"}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-end gap-1">
      <Input
        type="number"
        step="0.01"
        min="0"
        max="1"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-7 w-20 text-xs"
        placeholder="0.85"
      />
      <Button
        size="sm"
        className="h-7 text-xs"
        disabled={setMutation.isPending}
        onClick={() => {
          const parsed = parseFloat(value);
          setMutation.mutate(Number.isFinite(parsed) ? parsed : null);
        }}
      >
        Save
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 text-xs"
        disabled={setMutation.isPending}
        onClick={() => setMutation.mutate(null)}
        title="Clear the override; agent reverts to learned/global threshold"
      >
        Clear
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 text-xs"
        onClick={() => setEditing(false)}
      >
        Cancel
      </Button>
    </div>
  );
}
