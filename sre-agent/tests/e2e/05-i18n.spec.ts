import { expect, test } from "@playwright/test";
import { authenticate } from "./support";

test("switching to French swaps every visible string on the cluster view", async ({ page }) => {
  await authenticate(page);
  await page.goto("/cluster");

  // English baseline.
  await expect(page.getByText("Cluster overview", { exact: true })).toBeVisible();

  // Switch the locale via the top-bar Select (Radix Select).
  await page.getByLabel("Language").click();
  await page.getByRole("option", { name: "FR" }).click();

  // French labels appear immediately.
  await expect(page.getByText("Vue d'ensemble du cluster", { exact: true })).toBeVisible();
  await expect(page.getByText("Nœuds", { exact: true })).toBeVisible();
  await expect(page.getByText("Espaces de noms", { exact: true })).toBeVisible();

  // Persisted across reload.
  await page.reload();
  await expect(page.getByText("Vue d'ensemble du cluster", { exact: true })).toBeVisible();

  // Reset to EN so other tests aren't surprised.
  await page.getByLabel("Langue").click();
  await page.getByRole("option", { name: "EN" }).click();
});
