import { useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Activity, Boxes, GitBranch, Layers, ShieldCheck, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PRODUCT_NAME,
  TAGLINE_EN,
  TAGLINE_FR,
  BRAND_FOOTER_EN,
  BRAND_FOOTER_FR,
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
  const footer = locale === "fr" ? BRAND_FOOTER_FR : BRAND_FOOTER_EN;

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
          "fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r bg-card transition-transform duration-200",
          mobileNavOpen ? "translate-x-0" : "-translate-x-full",
          // Desktop: static, no transform, always visible.
          "md:static md:z-auto md:w-56 md:translate-x-0",
        )}
        aria-label="Primary navigation"
      >
        <div className="flex items-start justify-between border-b p-4">
          <div className="min-w-0">
            <div className="truncate text-base font-bold leading-tight tracking-tight">
              {PRODUCT_NAME}
            </div>
            <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{tagline}</div>
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

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
          {items.map(({ to, icon: Icon, key }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{t(key)}</span>
            </NavLink>
          ))}
        </nav>

        <div className="border-t p-3 text-[10px] uppercase tracking-wider text-muted-foreground">
          {footer}
        </div>
      </aside>
    </>
  );
}
