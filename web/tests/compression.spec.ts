import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });
const joint = process.env.CUTBACK_JOINT_CONTEXT_BROWSER === "true";

test("context compression, lost queue acknowledgement, confirmation and scoped source review", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_COMPRESSION_BROWSER !== "true",
    "Requires owned synthetic worker and PostgreSQL",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Compression", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Context compression", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Question dataset", { exact: true })
    .selectOption(process.env.CUTBACK_COMPRESSION_DATASET!);
  await page
    .getByLabel("Source configuration inspection (optional)")
    .setInputFiles(process.env.CUTBACK_COMPRESSION_INSPECTION!);
  await expect(
    page.getByRole("status").filter({ hasText: "Bound source configuration:" }),
  ).toBeVisible();
  await page.getByLabel("Minimum independent questions").fill("3");
  await page.getByLabel("Assumed CPU USD per hour (optional)").fill("0.01");
  await page
    .getByLabel(
      "Retain encrypted outputs and compressed input under the existing policy",
    )
    .check();
  await page
    .getByRole("button", {
      name: joint
        ? "Prepare model and context comparison"
        : "Prepare compression comparison",
      exact: true,
    })
    .click();
  const review = page.getByRole("region", {
    name: "Reviewed compression plan",
    exact: true,
  });
  await expect(review).toContainText("Budget fit: Yes");
  await review
    .getByRole("button", { name: "Approve this exact compression budget" })
    .click();
  let queues = 0;
  let acknowledgedLoss: () => void;
  const lost = new Promise<void>((resolve) => {
    acknowledgedLoss = resolve;
  });
  const queuePath = `**/v1/projects/${process.env.CUTBACK_UI_PROJECT}/experiments`;
  await page.route(queuePath, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    queues++;
    await route.fetch();
    await route.abort("connectionfailed");
    acknowledgedLoss();
  });
  await review
    .getByRole("button", { name: "Queue approved compression run" })
    .click();
  await lost;
  await expect(
    review.getByRole("button", { name: "Queue approved compression run" }),
  ).toBeDisabled();
  await page.unroute(queuePath);
  await review
    .getByRole("button", { name: "Reconcile plan execution" })
    .click();
  expect(queues).toBe(1);
  const results = page.getByRole("region", {
    name: "Compression run results",
    exact: true,
  });
  await expect(async () => {
    await page
      .getByRole("button", { name: "Refresh compression run", exact: true })
      .click();
    await expect(results).toContainText("COMPLETED");
  }).toPass({ timeout: 60000 });
  await expect(results).toContainText("answer quality requires review");
  if (joint) {
    await expect(results).toContainText("changed axes:");
    await expect(results).toContainText("gpt-4.1-mini");
    await expect(
      page.getByLabel("Search model and output ceiling alongside context"),
    ).toBeChecked();
  }
  await page
    .getByLabel("Purpose for reading case evidence")
    .fill("owned-compressed-input-review");
  await page
    .getByRole("button", { name: "Read authorized case outputs", exact: true })
    .click();
  const cases = page.getByRole("region", {
    name: "Encrypted case review",
    exact: true,
  });
  await cases
    .getByText("Compressed input for this native request", { exact: true })
    .first()
    .click();
  await expect(cases).toContainText("original context bytes");
  await expect(cases).toContainText("deadline-days=");
  await page
    .getByRole("button", { name: "Prepare selected candidate confirmation" })
    .click();
  await expect(review).toContainText("Review untouched-test confirmation");
  await review
    .getByRole("button", { name: "Approve this exact compression budget" })
    .click();
  await review
    .getByRole("button", { name: "Queue approved compression run" })
    .click();
  await expect(async () => {
    await page
      .getByRole("button", { name: "Refresh compression run", exact: true })
      .click();
    await expect(results).toContainText("COMPLETED");
  }).toPass({ timeout: 60000 });
  await page
    .getByLabel("Purpose for reading source proposal")
    .fill("owned-compression-source-review");
  await page
    .getByRole("button", { name: "Read authorized compression proposal" })
    .click();
  await expect(
    page.getByRole("region", { name: "Compression source proposal" }),
  ).toContainText("synthetic_local_review_only");
  if (joint)
    await expect(
      page.getByRole("heading", {
        name: "Review the combined model and context change",
      }),
    ).toBeVisible();
  expect(
    await page.evaluate(() =>
      [localStorage, sessionStorage].some((s) =>
        Object.values(s).some(
          (v) => v.includes("deadline-days=") || v.includes("Bearer "),
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
  await page
    .getByRole("button", { name: "Refresh compression history", exact: true })
    .click();
  await page
    .getByRole("region", { name: "Compression run history" })
    .getByRole("button", { name: /^COMPLETED ·/ })
    .first()
    .click();
  await expect(review).toHaveCount(0);
  await expect(results).toContainText("Review independent confirmation");
  expect(errors).toEqual([]);
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const view = await viewer.newPage();
  await view.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await view
    .getByLabel("Control API key")
    .fill(process.env.CUTBACK_COMPRESSION_VIEWER!);
  await view
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await view.getByRole("button", { name: "Compression", exact: true }).click();
  await expect(
    view.getByRole("heading", { name: "Context compression", exact: true }),
  ).toBeVisible();
  await expect(
    view.getByRole("button", {
      name: "Prepare compression comparison",
      exact: true,
    }),
  ).toHaveCount(0);
  expect((await new AxeBuilder({ page: view }).analyze()).violations).toEqual(
    [],
  );
  await viewer.close();
});
