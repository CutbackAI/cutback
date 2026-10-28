import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("authorized quality receipts, comparisons and explicit bounded approval", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Monitoring", exact: true }).click();
  const region = page.getByRole("region", {
    name: "Authorized online content quality",
  });
  await expect(
    region.getByRole("heading", {
      name: "Online content quality",
      exact: true,
    }),
  ).toBeVisible();
  await region
    .getByRole("button", {
      name: "Inspect quality rule: browser-content-check",
      exact: true,
    })
    .click();
  const investigation = region.getByRole("region", {
    name: "Quality rule investigation",
  });
  await expect(
    investigation.getByText("passed", { exact: true }).first(),
  ).toBeVisible();
  await investigation
    .getByRole("button", { name: "Investigate source trace" })
    .first()
    .click();
  await expect(
    investigation.getByRole("table", { name: /Trace tree and waterfall/ }),
  ).toBeVisible();
  const compare = investigation.getByRole("form", {
    name: "Compare observed quality cohorts",
  });
  const time = (hours: number) =>
    new Date(Date.now() + hours * 3600000).toISOString().slice(0, 16);
  await compare.getByLabel("Baseline start").fill(time(-5));
  await compare.getByLabel("Baseline end").fill(time(-3));
  await compare.getByLabel("Current start").fill(time(-2));
  await compare.getByLabel("Current end").fill(time(1));
  await compare
    .getByRole("button", { name: "Compare retained cohorts" })
    .click();
  await expect(
    region.getByRole("region", { name: "Observed cohort comparison" }),
  ).toContainText("population coverage are unknown");
  const form = region.getByRole("form", {
    name: "Approve online content quality rule",
  });
  await form
    .getByLabel("Quality rule key", { exact: true })
    .fill("browser-approved-quality");
  await form
    .getByLabel("Quality workflow", { exact: true })
    .fill("support.answer");
  const option = form
    .getByLabel("Source capture policy")
    .getByRole("option")
    .filter({ hasText: "support.answer" });
  await form
    .getByLabel("Source capture policy")
    .selectOption((await option.first().getAttribute("value"))!);
  await form.getByLabel("Rule duration in hours").fill("1");
  await form
    .getByLabel("Quality approval reference")
    .fill("owned-browser-approval");
  await form
    .getByLabel(
      "I authorize this scoped content use and the displayed request and dollar caps",
    )
    .check();
  await form
    .getByRole("button", { name: "Approve bounded quality rule" })
    .click();
  await expect(
    region.getByRole("heading", {
      name: "browser-approved-quality · v1",
      exact: true,
    }),
  ).toBeVisible();
  await expect(region).toContainText("disabled");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).concat(Object.keys(sessionStorage)),
    ),
  ).toEqual([]);
});
