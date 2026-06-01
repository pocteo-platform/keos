import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Check, X, HelpCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { api, type IncidentDetail, type UseCase } from "@/lib/api";

interface Props {
  useCaseId: string;
  incident: IncidentDetail;
}

/** Shown at the top of /pipeline/<id> when an incident was launched from a
 *  use case. Each expectation gets a ✓ / ✗ / ? next to it based on the
 *  actual outcome. Not a hard test — the LLM is non-deterministic — just
 *  a sanity-check during demos. */
export function ResultsBanner({ useCaseId, incident }: Props) {
  const { t } = useTranslation();
  const ucQ = useQuery({
    queryKey: ["usecase", useCaseId],
    queryFn: () =>
      api.listUseCases().then((items) => items.find((u) => u.id === useCaseId) ?? null),
  });

  if (!ucQ.data) return null;
  const uc = ucQ.data;
  const checks = evaluate(uc, incident);

  return (
    <Card className="border-info/40">
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold">{t("usecases.results.title")}</span>
          <span className="text-muted-foreground">·</span>
          <span className="font-mono text-xs">{uc.name}</span>
        </div>
        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {checks.map((c) => (
            <li key={c.key} className="flex items-start gap-2 text-xs">
              <Icon state={c.state} />
              <div>
                <div className="font-medium">{c.label}</div>
                <div className="text-muted-foreground">
                  {t("usecases.results.expected")}: <span className="font-mono">{c.expected}</span>
                  {c.actual !== c.expected ? (
                    <>
                      {" "}
                      · {t("usecases.results.actual")}:{" "}
                      <span className="font-mono">{c.actual}</span>
                    </>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

type State = "pass" | "fail" | "unset";
interface Check {
  key: string;
  label: string;
  expected: string;
  actual: string;
  state: State;
}

function evaluate(uc: UseCase, inc: IncidentDetail): Check[] {
  const out: Check[] = [];

  // Diagnosis keywords
  if (uc.expectation.diagnosis_keywords.length > 0) {
    const summary = (inc.diagnosis_summary ?? "").toLowerCase();
    const hits = uc.expectation.diagnosis_keywords.filter((k) => summary.includes(k.toLowerCase()));
    out.push({
      key: "kw",
      label: "diagnosis keywords",
      expected: uc.expectation.diagnosis_keywords.join(", "),
      actual: hits.length ? hits.join(", ") : "(none matched)",
      state: hits.length === uc.expectation.diagnosis_keywords.length ? "pass" : "fail",
    });
  }

  // Proposed tool
  if (uc.expectation.proposed_tool) {
    out.push({
      key: "tool",
      label: "proposed tool",
      expected: uc.expectation.proposed_tool,
      actual: inc.proposed_tool ?? "—",
      state: inc.proposed_tool === uc.expectation.proposed_tool ? "pass" : "fail",
    });
  }

  // Policy decision
  if (uc.expectation.policy_allow !== null && uc.expectation.policy_allow !== undefined) {
    out.push({
      key: "policy",
      label: "policy decision",
      expected: uc.expectation.policy_allow ? "allow" : "deny",
      actual: inc.policy_allow === true ? "allow" : inc.policy_allow === false ? "deny" : "—",
      state:
        inc.policy_allow === null
          ? "unset"
          : inc.policy_allow === uc.expectation.policy_allow
            ? "pass"
            : "fail",
    });
  }

  // Outcome
  if (uc.expectation.outcome) {
    const actual = inc.escalated ? "escalated" : inc.healed ? "healed" : "—";
    out.push({
      key: "outcome",
      label: "outcome",
      expected: uc.expectation.outcome,
      actual,
      state: actual === uc.expectation.outcome ? "pass" : actual === "—" ? "unset" : "fail",
    });
  }

  return out;
}

function Icon({ state }: { state: State }) {
  if (state === "pass") return <Check className="mt-0.5 h-4 w-4 shrink-0 text-ok" />;
  if (state === "fail") return <X className="mt-0.5 h-4 w-4 shrink-0 text-danger" />;
  return <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />;
}
