import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AgentTab } from "@/components/settings/AgentTab";
import { CalibrationTab } from "@/components/settings/CalibrationTab";
import {
  ForecastingTab,
  BaselinesTab,
  NotificationsTab,
  DriftTab,
} from "@/components/settings/RuntimeKnobsTab";
import { AboutTab } from "@/components/settings/AboutTab";
import { AuditTable } from "@/components/audit/AuditTable";
import { Card, CardContent } from "@/components/ui/card";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Phase 14.6 — Unified Settings.
 *
 * Replaces the old top-level `/control` and `/calibration` routes
 * with a single tabbed page so the operator finds every config
 * surface in one place. Tab is preserved in `?tab=` so deep links
 * survive (the redirects from /control + /calibration target the
 * matching tab).
 *
 *   AGENT          — mode toggle + pending approvals queue
 *   CALIBRATION    — per-rule thresholds (Phase 12)
 *   FORECASTING    — read-only env knobs (Phase 14)
 *   BASELINES      — read-only env knobs (Phase 13)
 *   NOTIFICATIONS  — read-only env knobs (Slack / generic / alertmgr)
 *   AUDIT          — full audit log explorer + chain verifier
 *   ABOUT          — version, license, edge positioning, the loop
 */
export default function SettingsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") ?? "agent";

  const auditQ = useQuery({
    queryKey: ["audit", { limit: 200 }],
    queryFn: () => api.listAudit({ limit: 200 }),
    refetchInterval: 8_000,
    enabled: tab === "audit",
  });

  return (
    <div className="flex flex-col gap-4">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          {t("settings.title", { defaultValue: "Settings" })}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("settings.subtitle", {
            defaultValue:
              "Agent kill-switch, calibration, runtime knobs, audit log, and product info.",
          })}
        </p>
      </header>
      <Tabs
        value={tab}
        onValueChange={(v) => setSearchParams((prev) => {
          const next = new URLSearchParams(prev);
          next.set("tab", v);
          return next;
        })}
        className="flex flex-col"
      >
        <TabsList className="flex w-full flex-wrap self-start">
          <TabsTrigger value="agent">
            {t("settings.tabs.agent", { defaultValue: "Agent" })}
          </TabsTrigger>
          <TabsTrigger value="calibration">
            {t("settings.tabs.calibration", { defaultValue: "Calibration" })}
          </TabsTrigger>
          <TabsTrigger value="forecasting">
            {t("settings.tabs.forecasting", { defaultValue: "Forecasting" })}
          </TabsTrigger>
          <TabsTrigger value="baselines">
            {t("settings.tabs.baselines", { defaultValue: "Baselines" })}
          </TabsTrigger>
          <TabsTrigger value="drift">
            {t("settings.tabs.drift", { defaultValue: "Drift" })}
          </TabsTrigger>
          <TabsTrigger value="notifications">
            {t("settings.tabs.notifications", { defaultValue: "Notifications" })}
          </TabsTrigger>
          <TabsTrigger value="audit">
            {t("settings.tabs.audit", { defaultValue: "Audit" })}
          </TabsTrigger>
          <TabsTrigger value="about">
            {t("settings.tabs.about", { defaultValue: "About" })}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="agent" className="mt-4">
          <AgentTab />
        </TabsContent>
        <TabsContent value="calibration" className="mt-4">
          <CalibrationTab />
        </TabsContent>
        <TabsContent value="forecasting" className="mt-4">
          <ForecastingTab />
        </TabsContent>
        <TabsContent value="baselines" className="mt-4">
          <BaselinesTab />
        </TabsContent>
        <TabsContent value="drift" className="mt-4">
          <DriftTab />
        </TabsContent>
        <TabsContent value="notifications" className="mt-4">
          <NotificationsTab />
        </TabsContent>
        <TabsContent value="audit" className="mt-4">
          <Card>
            <CardContent className="p-0">
              {auditQ.isLoading ? (
                <p className="p-4 text-sm text-muted-foreground">
                  {t("common.loading", { defaultValue: "Loading…" })}
                </p>
              ) : (auditQ.data?.items?.length ?? 0) === 0 ? (
                <p className="p-4 text-sm text-muted-foreground">
                  {t("settings.audit.empty", {
                    defaultValue: "No audit entries yet.",
                  })}
                </p>
              ) : (
                <AuditTable records={auditQ.data?.items ?? []} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="about" className="mt-4">
          <AboutTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
