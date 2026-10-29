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
    page.getByRole("table", { name: "Optimization candidate trade-offs" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Optimization trade-offs" }),
  ).toContainText("independent confirmation");
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
  await expect(
    page.locator(".paired-case").first().locator(".paired-outputs > section"),
  ).toHaveCount(3);
  await page
    .locator("summary")
    .filter({ hasText: "Review or correct this label" })
    .first()
    .click();
  await page
    .getByLabel("Correction review reference")
    .first()
    .fill("synthetic-browser-correction");
  const [correction] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/cases/correct") &&
        response.request().method() === "POST",
    ),
    page
      .getByRole("button", { name: "Create corrected dataset", exact: true })
      .first()
      .click(),
  ]);
  expect(correction.status()).toBe(201);
  await expect(
    page.getByText(
      "Created a new dataset. The original evidence is unchanged.",
      { exact: false },
    ),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Inspect technical-only result", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: "Paired model evaluation evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "compact: Technical checks passed; semantic quality unknown",
      exact: true,
    }),
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
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../artifacts/support-desk-first-screen.png",
    fullPage: false,
  });
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
  await page.setViewportSize({ width: 1365, height: 900 });
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await page
    .getByLabel("Purpose for reading source change")
    .fill("synthetic-patch-review");
  await page
    .getByRole("button", {
      name: "Read authorized diff and rollback",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Proposed change", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".patch-review pre").first()).toContainText(
    "gpt-4.1-mini",
  );
  await page.getByRole("button", { name: "Costs", exact: true }).click();
  await page.getByLabel("Traffic purpose").selectOption("application");
  await expect(
    page.getByRole("cell", { name: "16", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Group by").selectOption("workflow");
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await expect(
    page.getByRole("rowheader", { name: /app.py · triage/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("rowheader", { name: /app.py · needs_escalation/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Traces", exact: true }).click();
  const sample = await (await page.request.get("/v1/demo")).json();
  await page.getByText("Filter retained traces", { exact: true }).click();
  await page
    .getByLabel("Workflow identifier", { exact: true })
    .fill(sample.source_diff.source_binding.workflow_id);
  await page
    .getByRole("button", { name: "Apply trace filters", exact: true })
    .click();
  await expect(
    page.getByRole("table", { name: /Retained traces/ }).getByRole("row"),
  ).toHaveCount(3);
  await page.getByLabel("Only executions with errors").check();
  await expect(
    page.getByRole("table", { name: /Retained traces/ }).getByRole("row"),
  ).toHaveCount(2);
  await page.getByLabel("Only executions with errors").uncheck();
  await page
    .getByRole("button", { name: /Open trace/ })
    .first()
    .click();
  const candidates = page
    .getByLabel("Compare selected trace with")
    .locator("option");
  await expect(candidates).toHaveCount(2);
  await page
    .getByLabel("Compare selected trace with")
    .selectOption((await candidates.nth(1).getAttribute("value"))!);
  await expect(
    page.getByRole("table", { name: "Execution path differences" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("table", { name: "Execution path differences" })
      .getByRole("row"),
  ).toHaveCount(5);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Explore sample", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});
