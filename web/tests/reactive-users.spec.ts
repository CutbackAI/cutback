import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });
test("reactive user receipts, private trajectory, guided draft and mobile accessibility", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_REACTIVE_BROWSER !== "true",
    "Requires owned synthetic SDK worker and PostgreSQL",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const project = process.env.CUTBACK_UI_PROJECT!;
  await page.goto("/dashboard/?project=" + project);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page
    .getByRole("button", { name: "View reproducible evidence", exact: true })
    .click();
  const overhead = page.getByRole("region", {
    name: "User simulation for baseline",
    exact: true,
  });
  await expect(overhead).toContainText("simulator calls");
  await expect(overhead).toContainText("including simulation");
  await expect(page.getByText("You said:", { exact: false })).toHaveCount(0);
  await page
    .getByRole("button", { name: "Load execution receipts", exact: true })
    .click();
  const receipts = page.getByRole("region", {
    name: "Agent execution and call receipts",
  });
  await expect(receipts).toContainText("simulation");
  await page
    .getByLabel("Purpose for reading case evidence")
    .fill("synthetic-reactive-browser-review");
  await page
    .getByRole("button", { name: "Read authorized case outputs", exact: true })
    .click();
  const simulation = page
    .getByRole("group", { name: "Reactive user simulation receipt" })
    .first();
  await expect(simulation).toContainText("turn_cap");
  await expect(simulation).toContainText("1 requests");
  await expect(
    page
      .getByRole("list", { name: "Conversation and tool trajectory" })
      .first(),
  ).toContainText("You said:");
  await page.getByText("Plan an experiment", { exact: true }).click();
  await page
    .getByLabel("Add a reactive simulated user", { exact: true })
    .check();
  await page
    .getByLabel("Maximum reactive user turns", { exact: true })
    .fill("1");
  await page.getByLabel("Maximum experiment USD", { exact: true }).fill("1");
  const drafted = page.waitForResponse(
    (r) =>
      r.url().endsWith("/experiments/draft") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Freeze proposed plan", exact: true })
    .click();
  const response = await drafted;
  expect(response.status()).toBe(201);
  expect((await response.json()).evaluator_version).toBe("deterministic-v17");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  try {
    const other = await viewer.newPage();
    await other.goto("/dashboard/?project=" + project);
    await other
      .getByLabel("Control API key")
      .fill(process.env.CUTBACK_REACTIVE_VIEWER!);
    await other
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await other
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await other
      .getByRole("button", { name: "View reproducible evidence", exact: true })
      .click();
    await expect(
      other.getByRole("button", {
        name: "Read authorized case outputs",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(other.getByText("You said:", { exact: false })).toHaveCount(0);
    expect(
      (await new AxeBuilder({ page: other }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await viewer.close();
  }
  expect(errors).toEqual([]);
});
