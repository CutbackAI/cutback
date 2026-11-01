import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("authenticated API parity, navigation, accessibility and credential lifetime", async ({
  page,
}) => {
  const demoPause = async () => {
    if (process.env.CUTBACK_DEMO_VIDEO === "true")
      await page.waitForTimeout(1800);
  };
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto(
    "/dashboard/?project=" +
      encodeURIComponent(process.env.CUTBACK_UI_PROJECT || ""),
  );
  if (process.env.CUTBACK_DEMO_VIDEO === "true") {
    await page.evaluate(() => {
      const banner = document.createElement("div");
      banner.textContent =
        "CUTBACK DEMO — SYNTHETIC DATA — NO LIVE PROVIDER REQUESTS";
      banner.setAttribute("role", "region");
      banner.setAttribute("aria-label", "Synthetic demo disclosure");
      banner.style.cssText =
        "padding:12px;background:#222;color:white;text-align:center;font:14px sans-serif";
      document.body.prepend(banner);
    });
  }
  await expect(
    page.getByRole("heading", { name: /Understand your AI spend/ }),
  ).toBeVisible();
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("2", { exact: true })).toBeVisible();
  const a11y = await new AxeBuilder({ page }).analyze();
  expect(a11y.violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/dashboard-overview.png",
    fullPage: true,
  });
  await demoPause();
  await page.getByRole("button", { name: "Inventory", exact: true }).click();
  await expect(page.getByRole("cell", { name: /main.py:3/ })).toBeVisible();
  await demoPause();
  await page.getByRole("button", { name: "Costs", exact: true }).click();
  await expect(
    page.getByRole("table", { name: "Observed usage by model" }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: /Known modeled cost subtotals/ }),
  ).toBeVisible();
  await demoPause();
  await page
    .getByRole("button", { name: "Opportunities", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "resolve pricing" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Prepare with agent" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Let your coding agent handle the setup",
    }),
  ).toBeVisible();
  await expect(page.getByLabel("Request for your coding agent")).toHaveValue(
    /cutback optimize/,
  );
  await expect(page.getByLabel("Request for your coding agent")).toHaveValue(
    /workflow confirm/,
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await demoPause();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await expect(page.getByText("No experiment runs.")).toBeVisible();
  await demoPause();
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await expect(
    page.getByText(
      "No recommendations. Complete an approved experiment first.",
    ),
  ).toBeVisible();
  await demoPause();
  await page.getByRole("button", { name: "Integrations", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Instrumentation health" }),
  ).toBeVisible();
  await demoPause();
  await page.getByRole("button", { name: "Security", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Project keys" }),
  ).toBeVisible();
  await page.getByText("Issue project key", { exact: true }).click();
  await page.getByRole("button", { name: "Issue key", exact: true }).click();
  await expect(page.getByLabel("New project key")).toHaveAttribute(
    "type",
    "password",
  );
  await expect(page.getByLabel("New project key")).not.toHaveValue("");
  await page.getByRole("button", { name: "Clear key", exact: true }).click();
  await expect(page.getByLabel("New project key")).toHaveCount(0);
  await page
    .getByText("Retention and metadata export", { exact: true })
    .click();
  await page.getByLabel("Raw event retention days").fill("45");
  await page
    .getByRole("button", { name: "Save retention", exact: true })
    .click();
  await expect(
    page.getByText("Retention saved for the next maintenance cycle."),
  ).toBeVisible();
  const securityA11y = await new AxeBuilder({ page }).analyze();
  expect(securityA11y.violations).toEqual([]);
  await demoPause();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(page.url()).not.toContain(process.env.CUTBACK_UI_KEY!);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Security", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await demoPause();
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByLabel("Control API key")).toHaveValue("");
  expect(failures).toEqual([]);
});

test("guided project creation and role-aware connection use real collector endpoints", async ({
  page,
}) => {
  await page.goto("/dashboard/");
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByText("Create a project", { exact: true }).click();
  const slug = "browser-project-" + Date.now();
  await page.getByLabel("Project name", { exact: true }).fill(slug);
  await page
    .getByRole("button", { name: "Create project", exact: true })
    .click();
  await expect(
    page.getByLabel("Project", { exact: true }).locator("option:checked"),
  ).toHaveText(slug);
  await page.getByRole("button", { name: "Security", exact: true }).click();
  await page.getByText("Issue project key", { exact: true }).click();
  await page.getByLabel("Key role").selectOption("viewer");
  await page.getByRole("button", { name: "Issue key", exact: true }).click();
  const viewer = await page.getByLabel("New project key").inputValue();
  await page.getByRole("button", { name: "Disconnect" }).click();
  await page.getByLabel("Control API key").fill(viewer);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Security", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("Create a project", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("New project key")).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  await page.reload();
  await expect(page.getByLabel("Control API key")).toHaveValue("");
});

test("guided policy, CSV dataset, frozen plan approval, queue and cancellation require no raw JSON editor", async ({
  page,
}) => {
  await page.goto(
    "/dashboard/?project=" +
      encodeURIComponent(process.env.CUTBACK_UI_PROJECT!),
  );
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Security", exact: true }).click();
  await page.getByText("Create scoped capture policy", { exact: true }).click();
  const policy = page.getByRole("form", { name: "Capture authorization" });
  await policy
    .getByLabel("Authorization reference")
    .fill("browser-synthetic-policy");
  await policy.getByLabel("Allowed workflows").fill("classify");
  await policy.getByLabel("Sampling percent").fill("100");
  await policy.getByLabel("Enable this policy", { exact: true }).check();
  await policy
    .getByLabel(
      "I am authorized to approve this data, purpose, vendors and retention.",
    )
    .check();
  await policy
    .getByRole("button", { name: "Create policy", exact: true })
    .click();
  await expect(policy.getByRole("status")).toContainText("Policy saved.");
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page.getByText("Import authorized examples", { exact: true }).click();
  const dataset = page.getByRole("form", { name: "Create frozen dataset" });
  const rows = Array.from(
    { length: 40 },
    (_, i) =>
      `Synthetic ${String.fromCharCode(65 + Math.floor(i / 26))}${String.fromCharCode(65 + (i % 26))} input,${i % 2 ? "A" : "B"}`,
  ).join("\n");
  await dataset.getByLabel("Example file").setInputFiles({
    name: "owned-synthetic.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("input,gold\n" + rows),
  });
  await dataset.getByLabel("Dataset workflow").fill("classify");
  await dataset
    .getByLabel("Provenance reference")
    .fill("owned-browser-fixture");
  await dataset
    .getByLabel("Gold labels have been independently validated.")
    .check();
  await dataset
    .getByLabel(
      "This file is authorized for this policy, purpose and provider scope.",
    )
    .check();
  const createdDatasetResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/datasets") &&
      response.request().method() === "POST" &&
      response.status() === 201,
  );
  await dataset
    .getByRole("button", { name: "Import and freeze dataset" })
    .click();
  const createdDataset = await (await createdDatasetResponse).json();
  await expect(dataset.getByRole("status")).toContainText(
    "Imported 40 examples",
  );
  await page.getByText("Plan an experiment", { exact: true }).click();
  const plan = page.getByRole("form", {
    name: "Compare authorized alternatives",
  });
  const selected = plan.getByLabel("Frozen dataset");
  await expect(
    selected.locator(`option[value="${createdDataset.id}"]`),
  ).toHaveCount(1);
  await plan.getByLabel("Frozen dataset").selectOption(createdDataset.id);
  await plan.getByLabel("Maximum experiment USD").fill("1");
  await plan.getByLabel("Minimum labeled metric percent").fill("80");
  await plan.getByRole("button", { name: "Freeze proposed plan" }).click();
  await expect(plan.getByRole("status")).toContainText("frozen.");
  await page
    .getByLabel("Approval reference (required to approve a budget)")
    .fill("browser-synthetic-budget");
  await page
    .getByRole("button", { name: "Approve this budget", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Queue approved run", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: /QUEUED/ })).toBeVisible();
  await page.getByRole("button", { name: "Cancel further calls" }).click();
  await expect(page.getByRole("heading", { name: /CANCELLED/ })).toBeVisible();
  await page.getByRole("button", { name: "Training", exact: true }).click();
  await expect(
    page.getByText(
      "No training jobs. Model evaluation does not require fine-tuning.",
    ),
  ).toBeVisible();
  const a11y = await new AxeBuilder({ page }).analyze();
  expect(a11y.violations).toEqual([]);
});
