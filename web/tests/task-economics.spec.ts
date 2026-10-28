import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("native task coverage, failed spend, unknown components and frozen receipts", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_TASK_SAMPLE !== "true",
    "Requires the owned native-task fixture",
  );
  const errors: string[] = [];
  let comparisonsCreated = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().endsWith("/improvement-observations")
    ) {
      comparisonsCreated++;
    }
  });
  page.on("pageerror", (e) => errors.push(e.message));
  let releaseMetadata = () => {};
  let metadataStarted = () => {};
  const hold = new Promise<void>((resolve) => {
    releaseMetadata = resolve;
  });
  const started = new Promise<void>((resolve) => {
    metadataStarted = resolve;
  });
  await page.route(/\/environments$/, async (route) => {
    const response = await route.fetch();
    metadataStarted();
    await hold;
    await route.fulfill({ response });
  });
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Task economics", exact: true })
    .click();
  await started;
  await page
    .getByLabel("Application release", { exact: true })
    .fill("synthetic-cheap-but-worse");
  await page.getByLabel("Expected tasks in window").fill("2");
  releaseMetadata();
  await page
    .getByRole("button", { name: "Read task economics", exact: true })
    .click();
  const table = page.getByRole("table", {
    name: "Application tasks and cost coverage",
  });
  await expect(table.locator("tbody tr")).toHaveCount(2);
  await expect(table.getByRole("cell", { name: /failure/ })).toBeVisible();
  await expect(table).toContainText("Retained graph consistent");
  await page.getByRole("button", { name: "Freeze task cost evidence" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Frozen receipt" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByLabel("Application release", { exact: true })
    .fill("synthetic-reference");
  await page
    .getByRole("button", { name: "Read task economics", exact: true })
    .click();
  await expect(table.locator("tbody tr")).toHaveCount(2);
  await page.getByRole("button", { name: "Freeze task cost evidence" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Frozen receipt" }),
  ).toBeVisible();
  await page
    .getByText("Compare frozen releases and optimization costs", {
      exact: true,
    })
    .click();
  const form = page.getByRole("form", { name: "Compare frozen task windows" });
  const options = form.getByLabel("Baseline frozen window").locator("option");
  const baselineId = await options
    .filter({ hasText: "synthetic-reference" })
    .getAttribute("value");
  const candidateId = await options
    .filter({ hasText: "synthetic-cheap-but-worse" })
    .getAttribute("value");
  await form.getByLabel("Baseline frozen window").selectOption(baselineId!);
  await form.getByLabel("Candidate frozen window").selectOption(candidateId!);
  await form.getByLabel("Forecast successful tasks").fill("100");
  await form.getByLabel("Minimum tasks per window").fill("2");
  await form.getByLabel(/I have listed all proposer/).check();
  await form.getByLabel(/Other one-time costs are complete/).check();
  await form
    .getByRole("button", { name: "Record economic comparison" })
    .click();
  const decision = page.getByRole("region", {
    name: "Improvement economic decision",
  });
  await expect(
    decision.getByRole("heading", { name: "Abstain or reject" }),
  ).toBeVisible();
  await expect(decision).toContainText("declared outcome regression");
  await expect(decision).toContainText(
    "Review the change and rollback evidence",
  );
  const history = page.getByRole("region", {
    name: "Saved economic comparisons",
  });
  const savedComparison = history.getByRole("button", {
    name: /Open comparison synthetic-reference/,
  });
  await expect(savedComparison).toBeVisible();
  await form.getByLabel("Forecast successful tasks").fill("101");
  await expect(decision).toHaveCount(0);
  await savedComparison.click();
  await expect(decision).toContainText("declared outcome regression");
  expect(comparisonsCreated).toBe(1);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page
    .getByLabel("Application release", { exact: true })
    .fill("synthetic-incomplete");
  await page.getByLabel("Expected tasks in window").fill("1");
  await expect(
    page.getByRole("status").filter({ hasText: "Frozen receipt" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Read task economics", exact: true })
    .click();
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(
    table.getByRole("cell", { name: "Unknown", exact: true }),
  ).toBeVisible();
  await expect(
    table.getByRole("cell", { name: "1", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/task-economics-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
