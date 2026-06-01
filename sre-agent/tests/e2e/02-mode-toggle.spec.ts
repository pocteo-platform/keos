import { expect, test } from "@playwright/test";
import { authenticate, readToken } from "./support";

test("mode toggle from observe-only to active flips the CRD", async ({ page, request }) => {
  await authenticate(page);

  // Reset via API so the toggle has a known starting point.
  const token = readToken();
  await request.post("/api/mode", {
    headers: { Authorization: `Bearer ${token}` },
    data: { mode: "observe-only" },
  });

  await page.goto("/control");

  // Click the Active toggle and confirm the dialog.
  await page.getByRole("button", { name: /^Active$/ }).click();
  await page.getByRole("button", { name: "Confirm" }).click();

  // Within 5s the mode-badge in the top bar (and the toggle) reflect "Active".
  await expect(
    page.getByRole("button", { name: /^Active$/, pressed: true }),
  ).toBeVisible({ timeout: 6000 });

  // The /api/mode endpoint independently confirms.
  const r = await request.get("/api/mode", { headers: { Authorization: `Bearer ${token}` } });
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.mode).toBe("active");

  // Reset for subsequent tests.
  await request.post("/api/mode", {
    headers: { Authorization: `Bearer ${token}` },
    data: { mode: "observe-only" },
  });
});
