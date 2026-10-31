import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });
test("scenario authoring, independent budget, private review and development import", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_SCENARIO_BROWSER !== "true",
    "Requires owned native mock worker and PostgreSQL",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const project = process.env.CUTBACK_UI_PROJECT!;
  await page.goto("/dashboard/?project=" + project);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  const region = page.getByRole("region", {
    name: "Generated development scenarios",
    exact: true,
  });
  await region.getByText("Expand training scenarios", { exact: true }).click();
  await region
    .getByRole("combobox", { name: "Scenario source dataset", exact: true })
    .selectOption(process.env.CUTBACK_SCENARIO_DATASET!);
  await region
    .getByLabel("Scenario task brief", { exact: true })
    .fill("Preserve synthetic support category and priority.");
  await region
    .getByRole("combobox", { name: "Scenario execution mode", exact: true })
    .selectOption("scripted_test_only");
  await region
    .getByLabel("Coverage goal 1", { exact: true })
    .fill("A hurried user asks for a concise response.");
  await region
    .getByRole("button", {
      name: "Freeze scenario generation plan",
      exact: true,
    })
    .click();
  const selected = region.getByRole("article", {
    name: "Selected scenario version",
    exact: true,
  });
  await expect(selected).toContainText("PENDING");
  await selected
    .getByLabel("Scenario budget approval reference", { exact: true })
    .fill("synthetic-browser-scenario-budget");
  await selected
    .getByRole("button", { name: "Approve exact scenario budget", exact: true })
    .click();
  await selected
    .getByRole("button", {
      name: "Queue approved scenario generation",
      exact: true,
    })
    .click();
  await expect
    .poll(async () => {
      await region
        .getByRole("button", { name: "Refresh scenarios", exact: true })
        .click();
      return selected.textContent();
    })
    .toContain("COMPLETED");
  await expect(selected).not.toContainText("Please keep the response concise.");
  await selected
    .getByLabel("Scenario content access purpose", { exact: true })
    .fill("synthetic-browser-owner-review");
  await selected
    .getByRole("button", { name: "Load private scenario content", exact: true })
    .click();
  await expect(selected).toContainText("Please keep the response concise.");
  await expect(selected).toContainText('"population_coverage": "unknown"');
  await selected
    .getByLabel("Scenario review reference", { exact: true })
    .fill("synthetic-browser-reviewed-variants");
  await selected
    .getByRole("button", { name: "Record scenario review", exact: true })
    .click();
  await expect(selected).not.toContainText("Please keep the response concise.");
  await selected
    .getByLabel("Scenario dataset import reference", { exact: true })
    .fill("synthetic-browser-development-import");
  await selected
    .getByRole("button", {
      name: "Import reviewed development dataset",
      exact: true,
    })
    .click();
  await expect(
    selected.getByRole("status").filter({ hasText: "Development dataset" }),
  ).toContainText("no test split");
  const scenarioId = (await selected
    .getByRole("heading", { level: 3, name: /^Scenario [a-f0-9-]+$/ })
    .textContent())!.replace("Scenario ", "");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).toEqual({ local: 0, session: 0 });
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  try {
    const other = await viewer.newPage();
    await other.goto("/dashboard/?project=" + project);
    await other
      .getByLabel("Control API key")
      .fill(process.env.CUTBACK_SCENARIO_VIEWER!);
    await other
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await other
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    const view = other.getByRole("region", {
      name: "Generated development scenarios",
      exact: true,
    });
    await view
      .getByRole("button", {
        name: "Inspect scenario " + scenarioId,
        exact: true,
      })
      .click();
    await expect(
      view.getByRole("button", {
        name: "Load private scenario content",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      view.getByRole("button", {
        name: "Freeze scenario generation plan",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(
      (await new AxeBuilder({ page: other }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await viewer.close();
  }
  expect(errors).toEqual([]);
});
