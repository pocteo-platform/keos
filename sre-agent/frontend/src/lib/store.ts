import { create } from "zustand";
import { clearStoredToken, getStoredToken, setStoredToken } from "./auth";

// App-level UI state. Auth + sidebar + theme + locale.
// REST data lives in TanStack Query; this is for the bits that don't.

type Theme = "light" | "dark";
type Locale = "en" | "fr";

interface AppState {
  token: string | null;
  sidebarCollapsed: boolean;
  // Whether the sidebar drawer is open on mobile (<md breakpoint). Desktop
  // shows the sidebar inline always; this only affects mobile.
  mobileNavOpen: boolean;
  theme: Theme;
  locale: Locale;

  setToken: (token: string | null) => void;
  toggleSidebar: () => void;
  setMobileNavOpen: (open: boolean) => void;
  setTheme: (t: Theme) => void;
  setLocale: (l: Locale) => void;
}

const initialTheme = (): Theme => {
  try {
    const stored = localStorage.getItem("shp_theme") as Theme | null;
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* ignore */
  }
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  return prefersDark ? "dark" : "light";
};

const initialLocale = (): Locale => {
  try {
    const stored = localStorage.getItem("shp_locale") as Locale | null;
    if (stored === "en" || stored === "fr") return stored;
  } catch {
    /* ignore */
  }
  const nav = (navigator.language || "en").toLowerCase();
  return nav.startsWith("fr") ? "fr" : "en";
};

// Sidebar collapsed state persists across reloads so an operator who
// prefers the icon-only rail keeps it. Default is "expanded" on first
// visit to maximise discoverability.
const initialSidebarCollapsed = (): boolean => {
  try {
    return localStorage.getItem("shp_sidebar_collapsed") === "1";
  } catch {
    return false;
  }
};

export const useApp = create<AppState>((set) => ({
  token: getStoredToken(),
  sidebarCollapsed: initialSidebarCollapsed(),
  mobileNavOpen: false,
  theme: initialTheme(),
  locale: initialLocale(),

  setToken: (token) => {
    if (token) setStoredToken(token);
    else clearStoredToken();
    set({ token });
  },

  toggleSidebar: () =>
    set((s) => {
      const next = !s.sidebarCollapsed;
      try {
        localStorage.setItem("shp_sidebar_collapsed", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return { sidebarCollapsed: next };
    }),
  setMobileNavOpen: (mobileNavOpen) => set({ mobileNavOpen }),

  setTheme: (theme) => {
    try {
      localStorage.setItem("shp_theme", theme);
    } catch {
      /* ignore */
    }
    document.documentElement.classList.toggle("dark", theme === "dark");
    set({ theme });
  },

  setLocale: (locale) => {
    try {
      localStorage.setItem("shp_locale", locale);
    } catch {
      /* ignore */
    }
    set({ locale });
  },
}));

// Apply the initial theme to the <html> element on first import.
if (typeof document !== "undefined") {
  document.documentElement.classList.toggle("dark", initialTheme() === "dark");
}
