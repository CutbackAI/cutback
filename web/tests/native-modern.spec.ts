import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("approved modern plans expose their exact native request and price references", async ({ page }) => {
  test.skip(process.env.CUTBACK_NATIVE_MODERN !== "true", "Requires the owned modern campaign collector");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector", exact: true }).click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  const contract = page.getByRole("region", { name: /^Native execution contracts for plan / })
    .filter({ hasText: "Reasoning: medium" }).first();
  await expect(contract).toContainText("gpt-6-luna");
  await expect(contract).toContainText("Reasoning: medium");
  await expect(contract).toContainText("Tier: auto");
  await expect(contract).toContainText("Fast premium");
  await expect(contract).toContainText("openai-text-2026-10-28-v1");
  await expect(contract).toContainText("SHA-256:");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await contract.screenshot({ path: "../artifacts/native-contract-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await contract.screenshot({ path: "../artifacts/native-contract-mobile.png" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "Campaigns", exact: true }).click();
  await page.getByRole("button", { name: new RegExp(
    "Inspect .*" + process.env.CUTBACK_NATIVE_CAMPAIGN!.slice(0, 8) + "$"
  ) }).click();
  const campaign = page.getByRole("region", { name:
    "Native execution contracts for campaign " + process.env.CUTBACK_NATIVE_CAMPAIGN,
    exact: true,
  });
  await expect(campaign).toContainText("Reasoning: medium");
  await expect(campaign).toContainText("Reasoning: none");
  await expect(campaign).toContainText("Tier: default");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
});
