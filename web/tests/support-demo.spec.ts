import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("newcomer explores real sample decisions, unknown intervals, private cases and mobile", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_SUPPORT_DEMO !== "true",
    "Requires the isolated support-desk PostgreSQL fixture",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/");
  await page
    .getByRole("button", { name: "Explore sample", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Can this support desk use a cheaper model?",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Compare sample candidates", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "compact: Passes sample gates",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "unsafe: Not eligible", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Failed criteria: priority.", { exact: false }),
  ).toBeVisible();
  await page
    .getByLabel("Purpose for reading case evidence")
    .fill("sample-review");
  await page
    .getByRole("button", { name: "Read authorized case outputs" })
    .click();
  await expect(
    page
      .locator("summary")
      .filter({ hasText: /ticket-/ })
      .first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Inspect technical-only result", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: "Paired model evaluation evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Unknown", exact: true }).first(),
  ).toBeVisible();
  expect(errors).toEqual([]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByRole("button", { name: "Compare sample candidates", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "unsafe: Not eligible", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/support-desk-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/support-desk-mobile.png",
    fullPage: true,
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Explore sample", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});
