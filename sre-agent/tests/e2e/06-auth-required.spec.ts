import { expect, test } from "@playwright/test";

test("API rejects requests without a bearer token", async ({ request }) => {
  // /api/health is open (so liveness probes work).
  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();

  // Every other endpoint requires the bearer.
  const mode = await request.get("/api/mode");
  expect(mode.status()).toBe(401);

  const audit = await request.get("/api/audit");
  expect(audit.status()).toBe(401);

  const incidents = await request.get("/api/incidents");
  expect(incidents.status()).toBe(401);

  // A bad token is also rejected.
  const wrong = await request.get("/api/mode", { headers: { Authorization: "Bearer not-a-real-token" } });
  expect(wrong.status()).toBe(401);
});

test("SPA without a token shows the paste-token screen", async ({ page }) => {
  // Make sure no leftover token from previous tests.
  await page.context().clearCookies();
  await page.goto("/");
  await page.evaluate(() => localStorage.removeItem("shp_token"));
  await page.reload();
  await expect(page.getByText(/Paste your API token|Collez votre jeton/)).toBeVisible();
});
