import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 8.6 E2E config. Runs against the live docker-compose stack:
 *   make demo-ui    # spins up the whole stack and opens the SPA
 *
 * The token is read at test time from .env.local (gitignored).
 */
export default defineConfig({
  testDir: ".",
  fullyParallel: false, // serial — these mutate live cluster state
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "./playwright-report" }]],
  use: {
    baseURL: process.env.SHP_E2E_BASE ?? "http://localhost",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
