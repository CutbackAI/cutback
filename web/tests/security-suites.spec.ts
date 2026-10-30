import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("security and utility stay distinct with scoped preparation and RAM-only credentials", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_SECURITY_RUN,
    "Requires the actual paired security sample",
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
  const refusal = page.getByRole("region", {
    name: "Security and utility for refusal-control",
    exact: true,
  });
  const useful = page.getByRole("region", {
    name: "Security and utility for scoped-task-control",
    exact: true,
  });
  await expect(refusal).toContainText("insufficient evidence");
  await expect(refusal).toContainText("Safe but useless: 1");
  await expect(useful).toContainText("Safe and useful: 2");
  await expect(useful).toContainText(
    "General guardrail false-positive rate: Unknown",
  );
  await expect(
    refusal.getByRole("row", { name: /Task completed under attack/ }),
  ).toContainText("0.0%");
  await expect(
    useful.getByRole("row", { name: /Task completed under attack/ }),
  ).toContainText("100.0%");
  expect(await page.locator("body").innerText()).not.toContain(
    "CUTBACK_SYNTHETIC_CANARY_",
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
    .selectOption(process.env.CUTBACK_SECURITY_DATASET!);
  await expect(
    form.getByText("Paired security and utility acceptance", { exact: true }),
  ).toBeVisible();
  await expect(
    form.getByLabel("Minimum independent task families", { exact: true }),
  ).toHaveValue("20");
  await form
    .getByLabel("Tool service ID", { exact: true })
    .fill(process.env.CUTBACK_SECURITY_BROKER!);
  await form
    .getByLabel("Authorized record owner ID", { exact: true })
    .fill(process.env.CUTBACK_SECURITY_PRINCIPAL!);
  await form
    .getByLabel("Approved container image digest", { exact: true })
    .fill(process.env.CUTBACK_TEST_TOOL_IMAGE_ID!);
  await form
    .getByLabel(
      "I authorize the selected records to be used in this isolated tool service.",
      { exact: true },
    )
    .check();
  const response = page.waitForResponse(
    (response) =>
      response.url().endsWith("/experiments/draft") &&
      response.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Freeze proposed plan", exact: true })
    .click();
  const draft = await response;
  expect(draft.status()).toBe(201);
  const body = draft.request().postDataJSON();
  expect(body.security_suite.minimum_families).toBe(20);
  expect(body.security_suite.maximum_benign_utility_drop).toBe(0.05);
  expect(body.isolated_tools.content_use_approved).toBe(true);
  expect(
    JSON.stringify(
      await page.evaluate(() => ({
        local: { ...localStorage },
        session: { ...sessionStorage },
      })),
    ),
  ).not.toContain(key);
  expect(page.url()).not.toContain(key);
  await page
    .getByLabel("Project", { exact: true })
    .selectOption(process.env.CUTBACK_SECURITY_OTHER_PROJECT!);
  await expect(
    page.getByRole("region", {
      name: "Security and utility for refusal-control",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});
