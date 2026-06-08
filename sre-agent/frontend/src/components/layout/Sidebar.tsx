import { useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Boxes,
  ChevronsLeft,
  ChevronsRight,
  GitBranch,
  HeartPulse,
  Layers,
  LayoutDashboard,
  Settings as SettingsIcon,
  TrendingUp,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  COMPANY_NAME,
  PLATFORM_NAME,
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
} from "@/lib/branding";
import { useApp } from "@/lib/store";
import { api } from "@/lib/api";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * Phase 14.10 — Pro sidebar.
 *
 * Three operator personas, three nav sections — Komodor / Linear / Datadog
 * all converge on this layout because it scales from 5 to 50 nav items
 * without flattening to "wall of links".
 *
 *   OVERVIEW   — Dashboard, Reliability
 *   OPERATE    — Cluster, Incidents, Pipeline, Use Cases
 *   LEARN      — Optimization, Settings
 *
 * Collapsible: click the chevron in the header (or hit `[`) to switch
 * between the full-width `expanded` rail and the icon-only `collapsed`
 * rail (60px). State persists in localStorage. When collapsed each
 * NavLink is wrapped in a Tooltip so labels reappear on hover.
 *
 * The Incidents item carries a live active-count pill (4s poll); the
 * pill survives the collapse — when collapsed it floats over the top
 * right of the icon as a 6px pulsing dot.
 *
 * Three persistent UX affordances:
 *  1. Brand header (Keos / Pocteo platform attribution at bottom)
 *  2. Live agent-mode pill above the footer (observe-only / active /
 *     frozen) so the operator sees the kill-switch state across every
 *     route without leaving the page
 *  3. Mobile drawer affordance — the entire desktop layout collapses
 *     to a slide-in drawer below the md breakpoint
 */
const NAV_SECTIONS: ReadonlyArray<{
  label: string;
  items: ReadonlyArray<{
    to: string;
    icon: typeof LayoutDashboard;
    key: string;
  }>;
}> = [
  {
    label: "sidebar.section.overview",
    items: [
      { to: "/dashboard", icon: LayoutDashboard, key: "nav.dashboard" },
      { to: "/reliability", icon: HeartPulse, key: "nav.reliability" },
    ],
  },
  {
    label: "sidebar.section.operate",
    items: [
      { to: "/cluster", icon: Layers, key: "nav.cluster" },
      { to: "/incidents", icon: Activity, key: "nav.incidents" },
      { to: "/pipeline/live", icon: GitBranch, key: "nav.pipeline" },
      { to: "/usecases", icon: Boxes, key: "nav.usecases" },
    ],
  },
  {
    label: "sidebar.section.learn",
    items: [
      { to: "/optimization", icon: TrendingUp, key: "nav.optimization" },
      { to: "/settings", icon: SettingsIcon, key: "nav.settings" },
    ],
  },
] as const;

const MODE_TONE: Record<string, { rail: string; dot: string; label: string }> = {
  "observe-only": {
    rail: "border-l-info bg-info/10",
    dot: "bg-info",
    label: "modes.observe-only",
  },
  active: { rail: "border-l-ok bg-ok/10", dot: "bg-ok animate-pulse", label: "modes.active" },
  frozen: { rail: "border-l-warn bg-warn/10", dot: "bg-warn", label: "modes.frozen" },
};

export function Sidebar() {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const collapsed = useApp((s) => s.sidebarCollapsed);
  const toggleSidebar = useApp((s) => s.toggleSidebar);
  const mobileNavOpen = useApp((s) => s.mobileNavOpen);
  const setMobileNavOpen = useApp((s) => s.setMobileNavOpen);
  const location = useLocation();
  const tagline = locale === "fr" ? TAGLINE_FR : TAGLINE_EN;

  // Active-incidents counter pill. Polled every 4s; shown on the
  // Incidents nav item only.
  const activeQ = useQuery({
    queryKey: ["active-incidents"],
    queryFn: api.listActiveIncidents,
    refetchInterval: 4_000,
  });
  const activeCount = activeQ.data?.length ?? 0;

  // Live agent mode for the footer pill — same data the TopBar shows
  // but on every route, in the persistent rail. 8s poll is more than
  // enough since mode flips are rare operator actions.
  const modeQ = useQuery({
    queryKey: ["mode", "sidebar"],
    queryFn: api.getMode,
    refetchInterval: 8_000,
  });
  const modeTone = MODE_TONE[modeQ.data?.mode ?? "observe-only"] ?? MODE_TONE["observe-only"];

  // Close the mobile drawer on route change so navigating doesn't
  // leave it stuck open over the new page.
  useEffect(() => {
    if (mobileNavOpen) setMobileNavOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  // ESC closes the mobile drawer.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen, setMobileNavOpen]);

  // `[` toggles the desktop collapse — Linear-style. Skip when the
  // operator is typing in an input / textarea / contenteditable.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "[" || e.metaKey || e.ctrlKey || e.altKey) return;
      const tgt = e.target as HTMLElement | null;
      if (tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable))
        return;
      e.preventDefault();
      toggleSidebar();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleSidebar]);

  // When mobile drawer is open we force the EXPANDED layout so labels
  // are readable in the drawer — collapsed-rail only makes sense on
  // desktop.
  const showCollapsed = collapsed && !mobileNavOpen;

  return (
    <>
      {/* Backdrop: mobile-only, only when drawer is open. Tap to close. */}
      {mobileNavOpen ? (
        <button
          type="button"
          aria-label={t("topbar.closeMenu", "Close menu")}
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      ) : null}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex shrink-0 flex-col border-r bg-card transition-[transform,width] duration-200",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full",
          "md:static md:z-auto md:translate-x-0",
          // Width is the headline visual: 64 expanded, 16 collapsed.
          // 16 (= 64px) gives the icon rail enough breathing room for
          // a 40px tile + 12px of margin without feeling cramped.
          showCollapsed ? "w-16 md:w-16" : "w-64 md:w-60",
        )}
        aria-label="Primary navigation"
      >
        {/* ─── Header ───────────────────────────────────────────────
            Logo + product hierarchy. When collapsed, only the logo
            remains — the rest of the metadata is folded behind a
            tooltip on hover. */}
        <div className="relative border-b">
          <div
            className={cn(
              "flex items-start gap-3 transition-[padding] duration-200",
              showCollapsed ? "p-2.5" : "p-4",
            )}
          >
            <img
              src="/keos-shape.jpg"
              alt={PLATFORM_NAME}
              width={36}
              height={36}
              className="shrink-0 select-none rounded"
              draggable={false}
            />
            {showCollapsed ? null : (
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-1.5">
                  <span className="text-lg font-bold leading-none tracking-tight text-foreground">
                    {PLATFORM_NAME}
                  </span>
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    edge OS
                  </span>
                </div>
                <div className="mt-1 text-[13px] font-medium leading-tight text-foreground">
                  {PRODUCT_NAME.replace(`${PLATFORM_NAME} `, "")}
                </div>
                <div className="mt-1.5 line-clamp-2 text-[10px] leading-snug text-muted-foreground">
                  {tagline}
                </div>
              </div>
            )}
            {/* Mobile close button */}
            <button
              type="button"
              aria-label={t("topbar.closeMenu", "Close menu")}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground md:hidden"
              onClick={() => setMobileNavOpen(false)}
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Collapse toggle — desktop only; sits on the right border. */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={toggleSidebar}
                aria-label={
                  collapsed
                    ? t("sidebar.expand", { defaultValue: "Expand sidebar" })
                    : t("sidebar.collapse", { defaultValue: "Collapse sidebar" })
                }
                className="absolute -right-3 top-5 hidden h-6 w-6 items-center justify-center border bg-card text-muted-foreground shadow-sm transition-colors hover:bg-primary hover:text-primary-foreground md:flex"
              >
                {collapsed ? (
                  <ChevronsRight className="h-3.5 w-3.5" />
                ) : (
                  <ChevronsLeft className="h-3.5 w-3.5" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">
              {collapsed
                ? t("sidebar.expand", { defaultValue: "Expand sidebar" })
                : t("sidebar.collapse", { defaultValue: "Collapse sidebar" })}
              <kbd className="ml-1.5 border border-border bg-muted px-1 text-[9px] font-mono">
                [
              </kbd>
            </TooltipContent>
          </Tooltip>
        </div>

        {/* ─── Nav ───────────────────────────────────────────────────
            Sectioned. In expanded mode each section gets its caps
            label; in collapsed mode the section header disappears
            and a thin border separator marks the boundary instead. */}
        <nav
          className={cn(
            "scrollbar-thin flex flex-1 flex-col overflow-y-auto overflow-x-hidden",
            showCollapsed ? "gap-1 py-3" : "gap-1 px-2 py-3",
          )}
          aria-label="Sections"
        >
          {NAV_SECTIONS.map((section, sectionIdx) => (
            <div
              key={section.label}
              className={cn(
                "flex flex-col",
                showCollapsed
                  ? // Section spacing in collapsed mode: 12px gap +
                    // a thin top divider between groups, except the
                    // first group which sits flush under the header.
                    sectionIdx > 0
                    ? "mt-1.5 gap-0.5 border-t border-border/50 pt-1.5"
                    : "gap-0.5"
                  : "gap-0.5",
              )}
            >
              {showCollapsed ? null : (
                <div className="px-2 pb-1 pt-2 text-[9px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/60">
                  {t(section.label, {
                    defaultValue: section.label.split(".").pop() ?? "",
                  })}
                </div>
              )}
              {section.items.map(({ to, icon: Icon, key }) => {
                const showCounter = to === "/incidents" && activeCount > 0;
                const label = t(key);
                // Native HTML title attribute when collapsed — Tooltip
                // + asChild on NavLink was producing missing-icon
                // weirdness; the browser's own tooltip is bulletproof.
                const titleAttr = showCollapsed
                  ? showCounter
                    ? `${label} · ${activeCount} active`
                    : label
                  : undefined;
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === "/dashboard"}
                    title={titleAttr}
                    className={({ isActive }) =>
                      cn(
                        "group relative flex items-center font-medium transition-colors",
                        showCollapsed
                          ? cn(
                              "mx-2 h-10 justify-center rounded-sm text-sm",
                              isActive
                                ? "bg-primary text-primary-foreground shadow-sm"
                                : "text-foreground hover:bg-primary/10 hover:text-primary",
                            )
                          : cn(
                              "gap-3 px-2 py-2 text-sm",
                              isActive
                                ? "bg-primary text-primary-foreground"
                                : "text-muted-foreground hover:bg-primary/10 hover:text-primary",
                            ),
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon
                          strokeWidth={showCollapsed ? 2.25 : 2}
                          // Belt-and-braces: inline w/h ensures the
                          // icon paints at 20×20 in collapsed mode
                          // even if a Tailwind utility gets purged.
                          // Inline color ONLY when inactive — the
                          // active state needs to inherit the
                          // primary-foreground white from the parent
                          // tile, otherwise the inline `color` wins
                          // and the active icon stays dark on the
                          // dark primary background (operator bug).
                          style={
                            showCollapsed
                              ? {
                                  width: 20,
                                  height: 20,
                                  color: isActive ? undefined : "hsl(var(--foreground))",
                                }
                              : undefined
                          }
                          className={cn("shrink-0", showCollapsed ? "h-5 w-5" : "h-4 w-4")}
                        />
                        {showCollapsed ? null : (
                          <span className="flex-1 truncate">{label}</span>
                        )}
                        {showCounter ? (
                          showCollapsed ? (
                            <span
                              className="absolute right-1 top-1 inline-block h-2 w-2 animate-pulse rounded-full bg-warn ring-2 ring-card"
                              aria-label={`${activeCount} active`}
                            />
                          ) : (
                            <span
                              className="inline-flex items-center gap-1.5 bg-warn/15 px-1.5 py-0.5 text-[10px] font-semibold text-warn"
                              aria-label={`${activeCount} active`}
                            >
                              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-warn" />
                              {activeCount}
                            </span>
                          )
                        ) : null}
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        {/* ─── Agent mode pill ───────────────────────────────────────
            Live, persistent across every page. The kill-switch state
            is the single most important fact an operator wants to
            confirm at a glance.  */}
        <div className="border-t px-2 py-2">
          {showCollapsed ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  className={cn(
                    "flex h-8 w-full items-center justify-center border-l-2",
                    modeTone.rail,
                  )}
                >
                  <span className={cn("inline-block h-2 w-2 rounded-full", modeTone.dot)} />
                </div>
              </TooltipTrigger>
              <TooltipContent side="right">
                {t("sidebar.modeLabel", { defaultValue: "Agent mode" })}:{" "}
                <span className="font-semibold">{t(modeTone.label)}</span>
              </TooltipContent>
            </Tooltip>
          ) : (
            <div
              className={cn(
                "flex items-center gap-2 border-l-2 px-2 py-1.5 text-[11px]",
                modeTone.rail,
              )}
            >
              <span className={cn("inline-block h-2 w-2 rounded-full", modeTone.dot)} />
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t("sidebar.modeLabel", { defaultValue: "Agent mode" })}
              </span>
              <span className="ml-auto font-semibold tabular-nums">
                {t(modeTone.label)}
              </span>
            </div>
          )}
        </div>

        {/* ─── Footer attribution ─────────────────────────────────── */}
        <a
          href="https://pocteo.platform"
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "group flex items-center gap-2 border-t text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
            showCollapsed ? "justify-center p-2.5" : "p-3",
          )}
          aria-label={`by ${COMPANY_NAME}`}
        >
          <img
            src="/pocteo-platform.png"
            alt=""
            height={18}
            className="shrink-0 opacity-80 transition-opacity group-hover:opacity-100"
            aria-hidden="true"
          />
          {showCollapsed ? null : (
            <span className="truncate">
              by <span className="font-semibold tracking-normal">{COMPANY_NAME}</span>
            </span>
          )}
        </a>
      </aside>
    </>
  );
}
