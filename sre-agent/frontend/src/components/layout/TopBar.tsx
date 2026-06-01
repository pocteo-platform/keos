import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Moon, Sun, LogOut, Menu } from "lucide-react";
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
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";

export function TopBar() {
  const { t } = useTranslation();
  const { token, setToken, theme, setTheme, locale, setLocale, setMobileNavOpen } = useApp();

  // Mode polled every 5s in addition to the WS push so the badge survives
  // a transient WS disconnect.
  const modeQ = useQuery({
    queryKey: ["mode"],
    queryFn: api.getMode,
    refetchInterval: 5000,
    enabled: Boolean(token),
  });

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-card px-3 sm:gap-4 sm:px-4">
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

      <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
        <ModeBadge mode={modeQ.data?.mode} />
        {token ? (
          <Badge variant="info" className="hidden sm:inline-flex">
            {t("topbar.tokenOk")}
          </Badge>
        ) : (
          <Badge variant="warn" className="hidden sm:inline-flex">
            {t("topbar.tokenMissing")}
          </Badge>
        )}
      </div>

      <Select value={locale} onValueChange={(v) => setLocale(v as "en" | "fr")}>
        <SelectTrigger className="w-20 sm:w-24" aria-label={t("topbar.language")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="en">EN</SelectItem>
          <SelectItem value="fr">FR</SelectItem>
        </SelectContent>
      </Select>

      <Button
        variant="outline"
        size="icon"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={t("topbar.theme")}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>

      {token ? (
        <Button
          variant="outline"
          size="icon"
          onClick={() => setToken(null)}
          aria-label={t("topbar.logout")}
        >
          <LogOut className="h-4 w-4" />
        </Button>
      ) : null}
    </header>
  );
}
