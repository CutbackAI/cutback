import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("metadata rule approval, incident ownership, evidence and shared views", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Evaluation rules", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/No data is not healthy/)).toBeVisible();
  await page
    .getByRole("button", {
      name: "Inspect window history: browser-execution-alert",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("region", { name: "Retained alert window history" }),
  ).toContainText("breaching");
  await page
    .getByRole("button", { name: /^Open incident / })
    .first()
    .click();
  const investigation = page.getByRole("region", {
    name: "Incident investigation",
  });
  await expect(investigation.getByText(/State: open/)).toBeVisible();
  await investigation
    .getByRole("button", { name: "Acknowledge incident" })
    .click();
  await expect(investigation.getByText(/State: acknowledged/)).toBeVisible();
  await investigation
    .getByLabel("Team owner reference")
    .fill("synthetic-oncall");
  await investigation.getByRole("button", { name: "Assign owner" }).click();
  await expect(investigation.getByText(/owner synthetic-oncall/)).toBeVisible();
  await investigation
    .getByRole("button", { name: /^Investigate retained trace/ })
    .click();
  await expect(
    page.getByRole("table", { name: /Trace tree and waterfall/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: /owned-monitoring-check/ }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await investigation
    .getByRole("button", { name: "Resolve incident", exact: true })
    .click();
  await expect(investigation.getByText(/State: resolved/)).toBeVisible();
  await page
    .getByRole("combobox", { name: "Incident state", exact: true })
    .selectOption("resolved");
  await page.getByLabel("Saved view key").fill("browser-monitoring-view");
  await page
    .getByRole("button", { name: "Save shared view", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "browser-monitoring-view", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Incident state", exact: true })
    .selectOption("open");
  await page
    .getByRole("button", { name: "browser-monitoring-view", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "Incident state", exact: true }),
  ).toHaveValue("resolved");
  await page.getByText("Create an online rule", { exact: true }).click();
  const form = page.getByRole("form", {
    name: "Versioned evaluation rule",
    exact: true,
  });
  await form.getByLabel("Rule key", { exact: true }).fill("browser-new-rule");
  await form
    .getByLabel("Approval reference", { exact: true })
    .fill("owned-browser-only");
  await form.getByRole("button", { name: "Approve metadata rule" }).click();
  await expect(
    page.getByRole("heading", {
      name: "browser-new-rule · version 1",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Disable rule: browser-new-rule",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Disable rule: browser-new-rule",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    ),
  ).not.toContain(process.env.CUTBACK_UI_KEY);
});
