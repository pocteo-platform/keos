import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useMatches } from "react-router-dom";
import {
  ChevronRight,
  LogOut,
  Menu,
  Moon,
  Sun,
  Wifi,
  WifiOff,
  Server,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { ModeBadge } from "@/components/kube/ModeBadge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";
import { useWsStatus } from "@/lib/ws";
import { cn } from "@/lib/utils";

/**
 * Phase 14.10 — Pro topbar.
 *
 * Layout (left → right):
 *
 *  [☰ mobile]  [Breadcrumb: page · subpage]   ……   [Health · WS] [Mode] [EN/FR] [Theme] [Logout]
 *
 * - Breadcrumb derives from the URL — Dashboard / Reliability / etc.
 *   On detail routes (incidents/:id, pipeline/:id, workload/:ns/:kind/:name)
 *   we append the id / target as a tail segment.
 * - WS pill: live, derived from the WS reactive state. Green dot when
 *   open, amber dot pulsing when connecting/reconnecting, red dot when
 *   closed.
 * - Health pill: API doctor-style summary from /api/health. Green when
 *   reachable, red when the API is unreachable.
 * - Mode pill: live agent mode (observe-only / active / frozen). The
 *   same data the Sidebar foot pill shows — we keep it in the topbar
 *   too because it's the kill-switch and operator attention is at the
 *   top of the page.
 */

/** Map a pathname → list of breadcrumb segments. */
function buildCrumbs(pathname: string, t: (k: string, fallback?: string) => string): {
  label: string;
  to?: string;
}[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [{ label: t("nav.dashboard", "Dashboard") }];

  // First segment maps to a top-level nav item — known names get the
  // localised label; unknown segments are passed through verbatim.
  const NAV_LABEL: Record<string, string> = {
    dashboard: "nav.dashboard",
    reliability: "nav.reliability",
    cluster: "nav.cluster",
    incidents: "nav.incidents",
    pipeline: "nav.pipeline",
    usecases: "nav.usecases",
    optimization: "nav.optimization",
    settings: "nav.settings",
    workload: "nav.cluster",
  };
  const first = parts[0];
  const firstLabelKey = NAV_LABEL[first];
  const crumbs: { label: string; to?: string }[] = [
    {
      label: firstLabelKey ? t(firstLabelKey, first) : first,
      to: parts.length > 1 ? `/${first}` : undefined,
    },
  ];

  // Detail / sub-segments. We only render the LAST one as the tail —
  // intermediate path parts like "live" or kind/ns/name are joined
  // into one human-readable string.
  if (parts.length > 1) {
    let tail = parts.slice(1).join(" / ");
    // /pipeline/live → "Live" (special case)
    if (first === "pipeline" && parts[1] === "live") tail = t("pipeline.live", "Live");
    crumbs.push({ label: tail });
  }
  return crumbs;
}

export function TopBar() {
  const { t } = useTranslation();
  const { token, setToken, theme, setTheme, locale, setLocale, setMobileNavOpen } = useApp();
  const location = useLocation();
  // Touch useMatches so React Router re-renders us on route change reliably.
  useMatches();
  const wsStatus = useWsStatus();

  const modeQ = useQuery({
    queryKey: ["mode"],
    queryFn: api.getMode,
    refetchInterval: 5000,
    enabled: Boolean(token),
  });

  // /api/health gives us a cheap connectivity probe to the API itself.
  // We polled every 8s so the WS-vs-HTTP divergence is visible (a
  // healthy API with a dead WS shows "API ok / WS reconnecting", which
  // tells the operator the issue is the WS not the API).
  const healthQ = useQuery({
    queryKey: ["health", "topbar"],
    queryFn: api.health,
    refetchInterval: 8_000,
    retry: false,
    enabled: Boolean(token),
  });
  const apiOk = !healthQ.isError && Boolean(healthQ.data);

  const crumbs = buildCrumbs(location.pathname, (k, fb) => t(k, { defaultValue: fb ?? k }));

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-card px-3 sm:gap-3 sm:px-4">
      {/* Hamburger: mobile-only. Opens the sidebar drawer. */}
      <Button
        variant="outline"
        size="icon"
        className="md:hidden"
        onClick={() => setMobileNavOpen(true)}
        aria-label={t("topbar.openMenu", "Open menu")}
      >
        <Menu className="h-4 w-4" />
      </Button>

      {/* Breadcrumb */}
      <nav
        aria-label="Breadcrumb"
        className="flex min-w-0 flex-1 items-center gap-1 truncate text-sm"
      >
        {crumbs.map((crumb, i) => (
          <span key={i} className="flex items-center gap-1 truncate">
            {i > 0 ? (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            ) : null}
            {crumb.to ? (
              <Link
                to={crumb.to}
                className={cn(
                  "truncate font-medium transition-colors hover:text-primary",
                  i === crumbs.length - 1 ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {crumb.label}
              </Link>
            ) : (
              <span
                className={cn(
                  "truncate",
                  i === crumbs.length - 1
                    ? "font-semibold text-foreground"
                    : "text-muted-foreground",
                )}
                aria-current={i === crumbs.length - 1 ? "page" : undefined}
              >
                {crumb.label}
              </span>
            )}
          </span>
        ))}
      </nav>

      {/* Right-side cluster of indicators + controls */}
      <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
        {/* API + WS health */}
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              className={cn(
                "hidden h-8 items-center gap-2 border px-2 text-[11px] sm:flex",
                apiOk ? "border-ok/40 bg-ok/5" : "border-danger/40 bg-danger/5",
              )}
            >
              <Server className={cn("h-3 w-3", apiOk ? "text-ok" : "text-danger")} />
              <span className="font-medium uppercase tracking-wider">
                {apiOk
                  ? t("topbar.apiOk", { defaultValue: "API" })
                  : t("topbar.apiDown", { defaultValue: "API down" })}
              </span>
              <span className="text-muted-foreground">·</span>
              {wsStatus === "open" ? (
                <span className="flex items-center gap-1 text-ok">
                  <Wifi className="h-3 w-3" />
                  WS
                </span>
              ) : wsStatus === "connecting" ? (
                <span className="flex items-center gap-1 text-warn">
                  <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-warn" />
                  WS…
                </span>
              ) : (
                <span className="flex items-center gap-1 text-danger">
                  <WifiOff className="h-3 w-3" />
                  WS
                </span>
              )}
            </div>
          </TooltipTrigger>
          <TooltipContent>
            {apiOk
              ? t("topbar.apiHealthTooltip", {
                  defaultValue: "API healthy · WebSocket bridge for live events",
                })
              : t("topbar.apiDownTooltip", {
                  defaultValue: "API unreachable. The agent's REST surface isn't responding.",
                })}
          </TooltipContent>
        </Tooltip>

        {/* Agent mode pill — clickable to jump straight to Settings/Agent */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Link
              to="/settings?tab=agent"
              aria-label={t("topbar.modeTooltip", {
                defaultValue: "Agent mode (click to manage)",
              })}
            >
              <ModeBadge mode={modeQ.data?.mode} />
            </Link>
          </TooltipTrigger>
          <TooltipContent>
            {t("topbar.modeTooltip", { defaultValue: "Agent mode · click to manage" })}
          </TooltipContent>
        </Tooltip>

        {/* Token status — collapses to icon-tinted dot on mobile */}
        {token ? null : (
          <Badge variant="warn" className="hidden text-[10px] sm:inline-flex">
            {t("topbar.tokenMissing")}
          </Badge>
        )}

        {/* Language */}
        <Select value={locale} onValueChange={(v) => setLocale(v as "en" | "fr")}>
          <SelectTrigger className="h-9 w-16 sm:w-20" aria-label={t("topbar.language")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="en">EN</SelectItem>
            <SelectItem value="fr">FR</SelectItem>
          </SelectContent>
        </Select>

        {/* Theme */}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={t("topbar.theme")}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {theme === "dark"
              ? t("topbar.themeLight", { defaultValue: "Switch to light theme" })
              : t("topbar.themeDark", { defaultValue: "Switch to dark theme" })}
          </TooltipContent>
        </Tooltip>

        {token ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                onClick={() => setToken(null)}
                aria-label={t("topbar.logout")}
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("topbar.logout")}</TooltipContent>
          </Tooltip>
        ) : null}
      </div>
    </header>
  );
}
