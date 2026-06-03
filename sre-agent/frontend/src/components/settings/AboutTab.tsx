import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Cpu, Database, FileText, GitBranch, Globe, Shield } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { COMPANY_NAME, PLATFORM_NAME, PRODUCT_NAME } from "@/lib/branding";

/**
 * Settings → About tab. Carries the v1 brand surface:
 *   - Product name + version
 *   - Edge-cluster positioning (NOT a SaaS)
 *   - Jetson edition note (first target hardware)
 *   - License + governance pointers
 *
 * This is the "what is Keos" tab — useful when an architect lands
 * on the SPA without context and needs to know what they're looking
 * at in five seconds.
 */
export function AboutTab() {
  const { t } = useTranslation();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("settings.about.product.title", { defaultValue: "Product" })}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Field
            icon={<BookOpen className="h-4 w-4" />}
            label={t("settings.about.product.name", { defaultValue: "Name" })}
            value={PRODUCT_NAME}
          />
          <Field
            icon={<Globe className="h-4 w-4" />}
            label={t("settings.about.product.platform", { defaultValue: "Platform" })}
            value={PLATFORM_NAME}
          />
          <Field
            icon={<Shield className="h-4 w-4" />}
            label={t("settings.about.product.company", { defaultValue: "Company" })}
            value={COMPANY_NAME}
          />
          <Field
            icon={<GitBranch className="h-4 w-4" />}
            label={t("settings.about.product.version", { defaultValue: "Version" })}
            value={health.data?.version ?? "—"}
          />
          <Field
            icon={<FileText className="h-4 w-4" />}
            label={t("settings.about.product.license", { defaultValue: "License" })}
            value="Apache-2.0"
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("settings.about.deployment.title", { defaultValue: "Deployment" })}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Field
            icon={<Database className="h-4 w-4" />}
            label={t("settings.about.deployment.scope", { defaultValue: "Scope" })}
            value={t("settings.about.deployment.scopeValue", {
              defaultValue: "Single-tenant edge cluster",
            })}
            hint={t("settings.about.deployment.scopeHint", {
              defaultValue: "One install per Kubernetes cluster — not a SaaS.",
            })}
          />
          <Field
            icon={<Cpu className="h-4 w-4" />}
            label={t("settings.about.deployment.firstTarget", {
              defaultValue: "First target hardware",
            })}
            value={t("settings.about.deployment.jetson", {
              defaultValue: "NVIDIA Jetson Orin Nano (GPU edition)",
            })}
            hint={t("settings.about.deployment.jetsonHint", {
              defaultValue:
                "Local LLM (Qwen2.5-7b q4_K_M) runs on CUDA; CPU fallback supported.",
            })}
          />
          <Field
            icon={<Shield className="h-4 w-4" />}
            label={t("settings.about.deployment.properties", {
              defaultValue: "Properties",
            })}
            value={
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="ok">{t("settings.about.props.autonomous", { defaultValue: "Autonomous" })}</Badge>
                <Badge variant="info">{t("settings.about.props.resilient", { defaultValue: "Resilient" })}</Badge>
                <Badge variant="info">{t("settings.about.props.secure", { defaultValue: "Secure" })}</Badge>
                <Badge variant="ok">{t("settings.about.props.smart", { defaultValue: "Smart" })}</Badge>
                <Badge variant="info">{t("settings.about.props.learning", { defaultValue: "Learning" })}</Badge>
              </div>
            }
          />
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            {t("settings.about.loop.title", { defaultValue: "The agent loop" })}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="grid grid-cols-1 gap-3 text-xs md:grid-cols-4">
            <Phase
              chip="01"
              name={t("settings.about.loop.observe", { defaultValue: "Observe" })}
              summary={t("settings.about.loop.observeDesc", {
                defaultValue:
                  "Kubelet events + K8sGPT diagnostics + Prometheus baselines feed the triage layer.",
              })}
              variant="warn"
            />
            <Phase
              chip="02"
              name={t("settings.about.loop.predict", { defaultValue: "Predict" })}
              summary={t("settings.about.loop.predictDesc", {
                defaultValue:
                  "Linear-regression forecaster on every baseline; 5/15/60-min horizons drive the diagnose prompt.",
              })}
              variant="info"
            />
            <Phase
              chip="03"
              name={t("settings.about.loop.heal", { defaultValue: "Heal" })}
              summary={t("settings.about.loop.healDesc", {
                defaultValue:
                  "Local Qwen-2.5-7b picks an action from the 10-tool catalog; OPA matrix gates execution.",
              })}
              variant="default"
            />
            <Phase
              chip="04"
              name={t("settings.about.loop.learn", { defaultValue: "Learn" })}
              summary={t("settings.about.loop.learnDesc", {
                defaultValue:
                  "Per-rule calibration updates after every incident — 90 %-median confidence target.",
              })}
              variant="ok"
            />
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}

function Field({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 shrink-0 text-muted-foreground">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
        <div className="font-mono text-sm">{value}</div>
        {hint ? <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div> : null}
      </div>
    </div>
  );
}

function Phase({
  chip,
  name,
  summary,
  variant,
}: {
  chip: string;
  name: string;
  summary: string;
  variant: "ok" | "warn" | "info" | "default";
}) {
  const rail: Record<string, string> = {
    ok: "border-l-ok",
    warn: "border-l-warn",
    info: "border-l-info",
    default: "border-l-border",
  };
  return (
    <li className={`border-l-4 pl-3 ${rail[variant]}`}>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] text-muted-foreground">{chip}</span>
        <span className="font-semibold uppercase tracking-wider">{name}</span>
      </div>
      <p className="mt-1 text-[11px] text-foreground/80">{summary}</p>
    </li>
  );
}
