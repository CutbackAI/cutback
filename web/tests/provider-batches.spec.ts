import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("batch storage and budget approval are exact and native results preserve missing latency", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_BATCH_SAMPLE !== "true",
    "Requires the owned native batch fixture",
  );
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  const key = process.env.CUTBACK_UI_KEY!;
  await page.getByLabel("Control API key").fill(key);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Provider batches", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Provider batch processing",
    exact: true,
  });
  await expect(
    panel.getByRole("heading", { name: "Provider batches", exact: true }),
  ).toBeVisible();
  await panel
    .getByLabel("Pending validation plan")
    .selectOption(process.env.CUTBACK_BATCH_PLAN!);
  await expect(
    panel.getByRole("button", { name: "Prepare batch approval", exact: true }),
  ).toBeDisabled();
  await panel.getByLabel(/I authorize provider-side storage/).check();
  const preparation = page.waitForResponse(
    (response) =>
      response.url().endsWith("/provider-batches") &&
      response.request().method() === "POST",
  );
  await panel
    .getByRole("button", { name: "Prepare batch approval", exact: true })
    .click();
  const prepared = await (await preparation).json();
  await panel
    .getByText(`Review batch ${prepared.id.slice(0, 8)}`, { exact: true })
    .click();
  const review = panel.getByRole("region", {
    name: `Frozen batch ${prepared.id.slice(0, 8)}`,
    exact: true,
  });
  await expect(review).toContainText(prepared.approval_hash);
  await expect(review).toContainText("2 USD");
  await expect(review).toContainText("200 maximum native requests");
  await review
    .getByLabel("Batch approval reference")
    .fill("owned-browser-approval");
  const approval = page.waitForResponse((response) =>
    response.url().endsWith(`/provider-batches/${prepared.id}/approve`),
  );
  await review
    .getByRole("button", {
      name: "Approve this batch budget and storage",
      exact: true,
    })
    .click();
  const response = await approval;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON().approval_hash).toBe(
    prepared.approval_hash,
  );
  await expect(review).toBeVisible();
  const cancellation = page.waitForResponse((response) =>
    response.url().endsWith(`/provider-batches/${prepared.id}/cancel`),
  );
  await review
    .getByRole("button", { name: "Request cancellation", exact: true })
    .click();
  expect((await cancellation).status()).toBe(200);
  await panel
    .getByText(
      `Review batch ${process.env.CUTBACK_COMPLETED_BATCH!.slice(0, 8)}`,
      { exact: true },
    )
    .click();
  const completed = panel.getByRole("region", {
    name: `Frozen batch ${process.env.CUTBACK_COMPLETED_BATCH!.slice(0, 8)}`,
    exact: true,
  });
  await expect(completed).toContainText("Per-request latency: unknown");
  await completed
    .getByText("Validation measurements and known costs", { exact: true })
    .click();
  await expect(
    completed.getByRole("table", {
      name: "Native batch validation measurements",
    }),
  ).toContainText("16 / 16");
  await expect(completed).toContainText("deleted, deleted");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(storage.includes(key)).toBe(false);
  expect(page.url().includes(key)).toBe(false);
  let release!: () => void;
  let captured!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    captured = resolve;
  });
  await page.route(
    `**/projects/${process.env.CUTBACK_UI_PROJECT}/provider-batches?*`,
    async (route) => {
      const original = await route.fetch();
      captured();
      await gate;
      await route.fulfill({ response: original });
    },
  );
  await panel
    .getByRole("button", { name: "Refresh batches", exact: true })
    .click();
  await started;
  await page
    .getByLabel("Project", { exact: true })
    .selectOption(process.env.CUTBACK_BATCH_OTHER_PROJECT!);
  await expect(panel).toContainText("No prepared batches on this page");
  release();
  await expect(
    panel.getByText(`Review batch ${prepared.id.slice(0, 8)}`, { exact: true }),
  ).toHaveCount(0);
  await expect(
    panel.getByLabel("I authorize provider-side storage", { exact: false }),
  ).not.toBeChecked();
  expect(failures).toEqual([]);
});
