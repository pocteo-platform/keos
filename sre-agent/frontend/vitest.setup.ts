// Imported by every test via `vitest.config.ts::test.setupFiles`.
// Loads jest-dom's custom matchers (`toBeInTheDocument`, `toHaveTextContent`,
// etc.) and stubs i18next so components render their `defaultValue` strings
// instead of crashing on missing init.

import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom doesn't ship `matchMedia` (it's a CSSOM-View thing). Stub it once
// here so every component that reads it (theme detection in store.ts,
// prefers-reduced-motion guards, etc.) loads without crashing.
if (typeof window !== "undefined" && !window.matchMedia) {
  // Use Object.defineProperty so even tests that reach for it via
  // `window.matchMedia` (vs `globalThis.matchMedia`) find the stub.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {}, // legacy alias
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// Minimal react-i18next stub. Tests that need real i18n behavior can patch
// it again locally; the vast majority just want `t("key", { defaultValue })`
// to return the default.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, options?: { defaultValue?: string } | string) => {
      if (typeof options === "string") return options;
      return options?.defaultValue ?? _key;
    },
    i18n: { changeLanguage: () => Promise.resolve(), language: "en" },
  }),
  Trans: ({ children }: { children: React.ReactNode }) => children,
  initReactI18next: { type: "3rdParty", init: () => {} },
}));
