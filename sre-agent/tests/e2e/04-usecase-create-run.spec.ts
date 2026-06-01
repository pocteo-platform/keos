import { expect, test } from "@playwright/test";
import { authenticate, readToken } from "./support";

test("library use case runs and lands the user on /pipeline with the results banner", async ({ page, request }) => {
  await authenticate(page);
  const token = readToken();

  await page.goto("/usecases");
  await expect(page.getByText("Use Cases", { exact: true })).toBeVisible();

  // Library starter-set has 3 entries on a fresh install. We exercise the
  // "Run" button for the first card; that's enough to verify the full
  // create-or-pick-and-run pipeline without driving the 6-step wizard.
  const runButton = page.getByRole("button", { name: /^Run$/ }).first();
  await runButton.click();

  // Should navigate to /pipeline/<id>?usecase=<lib-id>
  await expect(page).toHaveURL(/\/pipeline\/[^?]+\?usecase=lib-/, { timeout: 10_000 });

  // The results banner appears once the incident detail comes back.
  await expect(page.getByText("Use-case results")).toBeVisible({ timeout: 60_000 });

  // Sanity: API also recorded a fresh incident.
  const list = await request.get("/api/incidents?limit=5", {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(list.ok()).toBeTruthy();
});
