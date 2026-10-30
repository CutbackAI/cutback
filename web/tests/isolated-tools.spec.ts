import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("isolated SQL receipts, guided approval, tenant changes and RAM-only credentials", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_TOOL_RUN,
    "Requires the actual owned isolated-record sample",
  );
  const errors: string[] = [];
  page.on("pageerror", (failure) => errors.push(failure.message));
  const key = process.env.CUTBACK_UI_KEY!;
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(key);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page
    .getByRole("button", { name: "View reproducible evidence", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Isolated tool receipts",
    exact: true,
  });
  await panel
    .getByRole("button", { name: "Load isolated tool receipts", exact: true })
    .click();
  await expect(
    panel
      .getByRole("table", { name: "Isolated record sessions", exact: true })
      .locator("tbody tr"),
  ).toHaveCount(4);
  await expect(panel).toContainText("Complete task cost remains unknown");
  await expect(
    panel.getByRole("cell", { name: /Cleanup confirmed/ }),
  ).toHaveCount(4);
  await panel
    .getByRole("button", { name: /Inspect operations for session/ })
    .first()
    .click();
  await expect(
    panel
      .getByRole("table", { name: /Tool operations for/ })
      .locator("tbody tr"),
  ).toHaveCount(2);
  await expect(panel).toContainText("records_patch");
  expect(await page.locator("body").innerText()).not.toContain(
    "SYNTHETIC_HIDDEN_FIELD",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByText("Plan an experiment", { exact: true }).click();
  const form = page.getByRole("form", {
    name: "Compare authorized alternatives",
    exact: true,
  });
  await form
    .getByRole("combobox", { name: "Frozen dataset", exact: true })
    .selectOption(process.env.CUTBACK_TOOL_DATASET!);
  await expect(
    form.getByText("Approved isolated tool service", { exact: true }),
  ).toBeVisible();
  await form
    .getByLabel("Tool service ID", { exact: true })
    .fill(process.env.CUTBACK_TOOL_BROKER!);
  await form
    .getByLabel("Authorized record owner ID", { exact: true })
    .fill(process.env.CUTBACK_TOOL_PRINCIPAL!);
  await form
    .getByLabel("Approved container image digest", { exact: true })
    .fill(process.env.CUTBACK_TEST_TOOL_IMAGE_ID!);
  await form
    .getByLabel(
      "I authorize the selected records to be used in this isolated tool service.",
      { exact: true },
    )
    .check();
  const draft = page.waitForResponse(
    (response) =>
      response.url().endsWith("/experiments/draft") &&
      response.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Freeze proposed plan", exact: true })
    .click();
  const frozen = await draft;
  expect(frozen.status()).toBe(201);
  expect(
    frozen.request().postDataJSON().isolated_tools.content_use_approved,
  ).toBe(true);
  expect(frozen.request().postDataJSON().isolated_tools.broker_id).toBe(
    process.env.CUTBACK_TOOL_BROKER,
  );
  const retained = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(retained.includes(key)).toBe(false);
  expect(page.url().includes(key)).toBe(false);
  let release!: () => void, captured!: () => void, fulfilled!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    captured = resolve;
  });
  const finished = new Promise<void>((resolve) => {
    fulfilled = resolve;
  });
  await page.route(
    `**/experiments/${process.env.CUTBACK_TOOL_RUN}/tool-sessions?*`,
    async (route) => {
      const original = await route.fetch();
      captured();
      await gate;
      await route.fulfill({ response: original });
      fulfilled();
    },
    { times: 1 },
  );
  try {
    await panel
      .getByRole("button", {
        name: "Refresh isolated tool receipts",
        exact: true,
      })
      .click();
    await started;
    await page
      .getByLabel("Project", { exact: true })
      .selectOption(process.env.CUTBACK_TOOL_OTHER_PROJECT!);
    release();
    await finished;
    await page.unrouteAll({ behavior: "wait" });
    await expect(
      page.getByRole("table", {
        name: "Isolated record sessions",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
