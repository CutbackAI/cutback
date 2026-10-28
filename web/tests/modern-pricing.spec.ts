import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("price snapshots show exact lifetime rates, context premiums and source scope", async ({ page }) => {
  test.skip(process.env.CUTBACK_MODERN_PRICING !== "true", "Requires the owned pricing collector");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector", exact: true }).click();
  await page.getByRole("button", { name: "Security", exact: true }).click();
  const snapshots = page.getByRole("region", { name: "Observed price snapshots" });
  const table = snapshots.getByRole("table", { name: /openai-text-2026-10-28-v1/ });
  const row = table.getByRole("row").filter({ hasText: "gpt-6-luna" });
  await expect(row).toContainText("30m: 0.125");
  await expect(row).toContainText("12h: 0.25");
  await expect(row).toContainText("272000");
  await expect(row).toContainText("output ×1.5");
  await expect(snapshots).toContainText("target execution needs separate qualification");
  await expect(row.getByRole("link", { name: "Source for gpt-6-luna" })).toHaveAttribute(
    "href", "https://developers.openai.com/api/docs/models/gpt-6-luna");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const book = snapshots.locator("article").filter({ hasText: "openai-text-2026-10-28-v1" });
  await book.screenshot({ path: "../artifacts/modern-pricing-desktop.png" });
  const contracts = snapshots.getByRole("table", { name: "Project contract rates · USD per million tokens" });
  await expect(contracts.locator("tbody tr")).toHaveCount(50);
  await snapshots.getByRole("button", { name: "Load more project rates" }).click();
  await expect(contracts.locator("tbody tr")).toHaveCount(51);
  await page.getByText("Add effective-dated contract rate", { exact: true }).click();
  const form = page.getByRole("form", { name: "Project rate override" });
  await expect(form.getByLabel("Uncached input rate", { exact: true })).toHaveValue("");
  await form.getByRole("combobox", { name: /^Input token contract/ }).selectOption("modern");
  await form.getByLabel("Exact model ID", { exact: true }).fill("gpt-6-luna");
  await form.getByLabel("Service tier (optional)", { exact: true }).fill("default");
  for (const [label, value] of [
    ["Uncached input rate", "0.1"], ["Output rate", "0.5"], ["Cache read rate", "0.01"],
    ["30-minute cache write rate", "0.125"], ["12-hour cache write rate", "0.25"],
  ]) await form.getByLabel(label, { exact: true }).fill(value);
  const starts = new Date(Date.now() - 60000);
  await form.getByLabel("Effective from (local time)", { exact: true }).fill(
    new Date(starts.getTime() - starts.getTimezoneOffset() * 60000).toISOString().slice(0, 16));
  await form.getByRole("button", { name: "Save rate", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("Contract rate recorded.");
  await snapshots.getByRole("button", { name: "Load more project rates" }).click();
  await expect(contracts.locator("tbody tr")).toHaveCount(52);
  const saved = contracts.getByRole("row").filter({ hasText: "gpt-6-luna" });
  await expect(saved).toContainText("Tier: default");
  await expect(saved).toContainText("30m: 0.125");
  await expect(saved).toContainText("12h: 0.25");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await book.screenshot({ path: "../artifacts/modern-pricing-mobile.png" });
  expect(errors).toEqual([]);
});
