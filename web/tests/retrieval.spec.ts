import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("real corpus retrieval authoring, independent confirmation and scoped proposal", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_RETRIEVAL_BROWSER !== "true",
    "Requires owned synthetic worker and PostgreSQL",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Retrieval", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Retrieval optimization", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Corpus JSON", { exact: true })
    .setInputFiles(process.env.CUTBACK_RETRIEVAL_CORPUS!);
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "documents selected in memory" }),
  ).toContainText("documents selected in memory");
  await page
    .getByLabel("Source configuration inspection (optional)")
    .setInputFiles(process.env.CUTBACK_RETRIEVAL_INSPECTION!);
  await expect(
    page.getByRole("status").filter({ hasText: "Bound source configuration:" }),
  ).toBeVisible();
  await page.getByLabel("Minimum independent questions").fill("3");
  await page.getByLabel("Assumed CPU USD per hour (optional)").fill("0.01");
  await page
    .getByLabel(
      "Retain encrypted case outputs and retrieved passages under the existing policy",
    )
    .check();
  await page
    .getByLabel(
      "I authorize this corpus upload under the selected dataset’s content rights and capture policy.",
    )
    .check();
  await page
    .getByRole("button", { name: "Prepare retrieval comparison", exact: true })
    .click();
  const review = page.getByRole("region", {
    name: "Reviewed retrieval plan",
    exact: true,
  });
  await expect(review).toContainText("Budget fit: Yes");
  await review
    .getByRole("button", { name: "Approve this exact retrieval budget" })
    .click();
  let queues = 0;
  let acknowledgedLoss: () => void;
  const lostAcknowledgement = new Promise<void>((resolve) => {
    acknowledgedLoss = resolve;
  });
  const queuePath = `**/v1/projects/${process.env.CUTBACK_UI_PROJECT}/experiments`;
  await page.route(queuePath, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    queues++;
    await route.fetch(); // Commit the one approved run, then lose its acknowledgement.
    await route.abort("connectionfailed");
    acknowledgedLoss();
  });
  await review
    .getByRole("button", { name: "Queue approved retrieval run" })
    .click();
  await expect(
    review.getByRole("button", { name: "Queue approved retrieval run" }),
  ).toBeDisabled();
  await lostAcknowledgement;
  await page.unroute(queuePath);
  await review
    .getByRole("button", { name: "Reconcile plan execution" })
    .click();
  expect(queues).toBe(1);
  const results = page.getByRole("region", {
    name: "Retrieval run results",
    exact: true,
  });
  await expect(results).toBeVisible();
  await expect(async () => {
    await page
      .getByRole("button", { name: "Refresh retrieval run", exact: true })
      .click();
    await expect(results).toContainText("COMPLETED");
  }).toPass({ timeout: 60000 });
  await expect(results).toContainText("answer quality requires review");
  await page
    .getByLabel("Purpose for reading case evidence")
    .fill("synthetic-retrieved-context-review");
  await page
    .getByRole("button", { name: "Read authorized case outputs", exact: true })
    .click();
  const cases = page.getByRole("region", {
    name: "Encrypted case review",
    exact: true,
  });
  await cases
    .getByText("Retrieved passages for this native request", { exact: true })
    .first()
    .click();
  await expect(cases).toContainText("context bytes");
  await expect(cases).toContainText("deadline-days=");
  expect(
    await page.evaluate(() => (globalThis as any).syntheticCorpusExecuted),
  ).toBeUndefined();
  await page
    .getByRole("button", { name: "Prepare selected candidate confirmation" })
    .click();
  await expect(review).toContainText("Review untouched-test confirmation");
  await review
    .getByRole("button", { name: "Approve this exact retrieval budget" })
    .click();
  await review
    .getByRole("button", { name: "Queue approved retrieval run" })
    .click();
  await expect(async () => {
    await page
      .getByRole("button", { name: "Refresh retrieval run", exact: true })
      .click();
    await expect(results).toContainText("COMPLETED");
  }).toPass({ timeout: 60000 });
  await expect(results).toContainText("Review independent confirmation");
  await page
    .getByLabel("Proposal review purpose")
    .fill("owned-synthetic-browser-review");
  await page
    .getByRole("button", { name: "Prepare source configuration proposal" })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Retrieval source proposal" }),
  ).toContainText("synthetic local review only");
  expect(
    await page.evaluate(() =>
      [localStorage, sessionStorage].some((storage) =>
        Object.values(storage).some((value) =>
          value.includes("deadline-days="),
        ),
      ),
    ),
  ).toBe(false);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
  await page
    .getByRole("button", { name: "Refresh retrieval history", exact: true })
    .click();
  await page
    .getByRole("button", { name: /^Confirmation · COMPLETED ·/ })
    .click();
  await expect(review).toHaveCount(0);
  await expect(results).toContainText("Review independent confirmation");
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const view = await viewer.newPage();
  await view.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await view
    .getByLabel("Control API key")
    .fill(process.env.CUTBACK_RETRIEVAL_VIEWER!);
  await view
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await view.getByRole("button", { name: "Retrieval", exact: true }).click();
  await expect(
    view.getByRole("heading", { name: "Retrieval optimization", exact: true }),
  ).toBeVisible();
  await expect(
    view.getByRole("button", {
      name: "Prepare retrieval comparison",
      exact: true,
    }),
  ).toHaveCount(0);
  expect((await new AxeBuilder({ page: view }).analyze()).violations).toEqual(
    [],
  );
  await viewer.close();
});
