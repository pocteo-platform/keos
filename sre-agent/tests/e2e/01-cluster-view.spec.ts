import { expect, test } from "@playwright/test";
import { authenticate } from "./support";

test("cluster view renders the four stat cards and the workloads namespace", async ({ page }) => {
  await authenticate(page);
  await page.goto("/cluster");

  // Four stat cards (labels are i18n keys; assert by visible text in EN).
  await expect(page.getByText("Cluster overview", { exact: true })).toBeVisible();
  await expect(page.getByText("Nodes", { exact: true })).toBeVisible();
  await expect(page.getByText("Namespaces", { exact: true })).toBeVisible();
  await expect(page.getByText("Running pods", { exact: true })).toBeVisible();
  await expect(page.getByText("Agent mode", { exact: true })).toBeVisible();

  // The namespace picker exists; the workloads pods are visible (or the
  // empty-state message points the user at `make cluster`).
  const podVisible = await page
    .locator("text=/nginx-demo-/")
    .first()
    .isVisible()
    .catch(() => false);
  const emptyVisible = await page
    .getByText("No pods in this namespace")
    .isVisible()
    .catch(() => false);
  expect(podVisible || emptyVisible).toBe(true);
});
