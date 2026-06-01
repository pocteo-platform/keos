import { useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Activity, Boxes, GitBranch, Layers, ShieldCheck, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  COMPANY_NAME,
  PLATFORM_NAME,
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
} from "@/lib/branding";
import { useApp } from "@/lib/store";

const items = [
  { to: "/cluster", icon: Layers, key: "nav.cluster" },
  { to: "/incidents", icon: Activity, key: "nav.incidents" },
  { to: "/pipeline/live", icon: GitBranch, key: "nav.pipeline" },
  { to: "/usecases", icon: Boxes, key: "nav.usecases" },
  { to: "/control", icon: ShieldCheck, key: "nav.control" },
] as const;

export function Sidebar() {
  const { t } = useTranslation();
  const locale = useApp((s) => s.locale);
  const mobileNavOpen = useApp((s) => s.mobileNavOpen);
  const setMobileNavOpen = useApp((s) => s.setMobileNavOpen);
  const location = useLocation();
  const tagline = locale === "fr" ? TAGLINE_FR : TAGLINE_EN;

  // Close the mobile drawer on route change so navigating doesn't leave it
  // stuck open over the new page.
  useEffect(() => {
    if (mobileNavOpen) setMobileNavOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  // Close on Escape for accessibility.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen, setMobileNavOpen]);

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
          // Mobile: fixed drawer, slide in/out from the left.
          "fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col bg-card transition-transform duration-200",
          // Subtle right edge (no shadow; sharp corners = sharp edges).
          "border-r",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full",
          // Desktop: static, no transform, always visible.
          "md:static md:z-auto md:w-60 md:translate-x-0",
        )}
        aria-label="Primary navigation"
      >
        {/* ─── Header ───────────────────────────────────────────────
            Logo + product hierarchy: the platform (Keos) on top, the
            specific product (SRE Agent) underneath, by-line at the
            bottom. Three lines of typography rhythm. */}
        <div className="border-b">
          <div className="flex items-start gap-3 p-4">
            <img
              src="/keos-logo.svg"
              alt={PLATFORM_NAME}
              width={36}
              height={36}
              className="shrink-0 select-none"
              draggable={false}
            />
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
            {/* Close button: mobile-only. */}
            <button
              type="button"
              aria-label={t("topbar.closeMenu", "Close menu")}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground md:hidden"
              onClick={() => setMobileNavOpen(false)}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* ─── Nav ───────────────────────────────────────────────────
            Brand-deep on active, brand-navy on hover. The CSS variables
            in globals.css pin --primary to brand-deep; `hover:bg-primary`
            with /90 alpha lands near brand-navy automatically. */}
        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto p-3">
          {items.map(({ to, icon: Icon, key }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-primary/10 hover:text-primary",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{t(key)}</span>
            </NavLink>
          ))}
        </nav>

        {/* ─── Footer attribution ─────────────────────────────────
            Tiny Pocteo Platform mark + name. Anchors the company
            identity without competing with the Keos branding at the
            top of the sidebar. */}
        <a
          href="https://pocteo.platform"
          target="_blank"
          rel="noopener noreferrer"
          className="group flex items-center gap-2 border-t p-3 text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <img
            src="/pocteo-platform-mark.svg"
            alt=""
            width={16}
            height={16}
            className="shrink-0 opacity-70 transition-opacity group-hover:opacity-100"
            aria-hidden="true"
          />
          <span className="truncate">
            by <span className="font-semibold tracking-normal">{COMPANY_NAME}</span>
          </span>
        </a>
      </aside>
    </>
  );
}
