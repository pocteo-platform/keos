import { expect, test } from "@playwright/test";
import { authenticate, readToken } from "./support";

test("running scenario 1 from the API shows up in the live pipeline + incidents list", async ({ page, request }) => {
  await authenticate(page);

  // Open the live pipeline first so the WS subscription is in place when
  // the scenario fires.
  await page.goto("/pipeline/live");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  // Dispatch via the API (faster + deterministic).
  const token = readToken();
  const r = await request.post("/api/scenarios/run", {
    headers: { Authorization: `Bearer ${token}` },
    data: { scenario_id: 1 },
  });
  expect(r.ok()).toBeTruthy();
  const incidentId = (await r.json()).incident_id as string;

  // Within 30 s the canvas should show at least one node moving past
  // "pending" (Triage almost certainly turns green first).
  await expect(page.locator("text=triage").first()).toBeVisible({ timeout: 30_000 });

  // The incident eventually surfaces in the Incidents list. Accept up to 3
  // minutes — LLM is on CPU. If this is flaky in CI, raise the timeout.
  await page.goto("/incidents");
  await expect(page.locator(`text=${incidentId}`)).toBeVisible({ timeout: 180_000 });
});
