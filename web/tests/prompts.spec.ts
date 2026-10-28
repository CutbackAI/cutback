import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("encrypted prompt authoring, local preview, frozen playground and exact alias rollback", async ({
  page,
}) => {
  const base = "/v1/projects/" + process.env.CUTBACK_UI_PROJECT;
  const headers = { Authorization: "Bearer " + process.env.CUTBACK_UI_KEY };
  const request = page.request;
  const policyResponse = await request.post(base + "/capture-policies", {
    headers,
    data: {
      enabled: true,
      approval_ref: "owned-prompt-browser",
      rights_basis: "synthetic",
      environments: ["staging"],
      workflows: ["classify"],
      allowed_providers: ["local"],
      sample_rate: 1,
      max_samples: 100,
      expires_at: new Date(Date.now() + 86400000).toISOString(),
    },
  });
  expect(policyResponse.status()).toBe(201);
  const policy = (await policyResponse.json()).id;
  const datasetResponse = await request.post(base + "/datasets", {
    headers,
    data: {
      workflow: "classify",
      policy_id: policy,
      environment: "staging",
      source_type: "synthetic_fixture",
      provenance_ref: "owned-prompt-browser",
      independent_labels: true,
      cases: Array.from({ length: 40 }, (_, i) => ({
        id: "case-" + i,
        input:
          "Owned browser case " +
          String.fromCharCode(65 + Math.floor(i / 26)) +
          String.fromCharCode(65 + (i % 26)),
        gold: "A",
      })),
    },
  });
  expect(datasetResponse.status()).toBe(201);
  const dataset = (await datasetResponse.json()).id;
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Prompts", exact: true }).click();
  await page.getByText("Create a prompt", { exact: true }).click();
  const create = page.getByRole("form", { name: "Prompt identity" });
  const key = "browser-prompt-" + Date.now();
  await create.getByLabel("Prompt key").fill(key);
  await create
    .getByRole("button", { name: "Create prompt", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: key, exact: true }),
  ).toBeVisible();
  const document = page.getByRole("form", { name: "Immutable prompt version" });
  await page.getByText("Author a new version", { exact: true }).click();
  await document
    .getByRole("combobox", { name: "Prompt content policy", exact: true })
    .selectOption(policy);
  await document.getByLabel("Prompt document (JSON)").fill(
    JSON.stringify({
      text: "First {{input}}",
      variables: { input: "string" },
    }),
  );
  await document
    .getByLabel(
      "This policy authorizes storing this prompt, schema and its components.",
    )
    .check();
  await document
    .getByRole("button", { name: "Save encrypted version" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Selected version: v1", exact: true }),
  ).toBeVisible();
  const records = await (
    await request.get(base + "/prompts", { headers })
  ).json();
  const prompt = records.items.find((r: any) => r.key === key);
  const version1 = (
    await (
      await request.get(base + "/prompts/" + prompt.id + "/versions", {
        headers,
      })
    ).json()
  ).items[0].id;
  await page.getByText("Promote selected version", { exact: true }).click();
  const promotion = page.getByRole("form", {
    name: "Reviewed alias promotion",
  });
  await promotion
    .getByLabel("Alias approval reference")
    .fill("owned-first-alias");
  await promotion.getByLabel(/I acknowledge that this change/).check();
  await promotion
    .getByRole("button", { name: "Promote exact version" })
    .click();
  await expect(promotion.getByRole("status")).toContainText("Alias changed");
  await document.getByLabel("Prompt document (JSON)").fill(
    JSON.stringify({
      text: "Second {{input}}",
      variables: { input: "string" },
    }),
  );
  await document
    .getByRole("button", { name: "Save encrypted version" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Selected version: v2", exact: true }),
  ).toBeVisible();
  await page
    .getByText("Read or preview authorized content", { exact: true })
    .click();
  const preview = page.getByRole("form", { name: "Render preview" });
  await preview
    .getByLabel("Preview variable values (JSON)")
    .fill(
      JSON.stringify({ input: "<script>window.__promptXss=true</script>" }),
    );
  await preview
    .getByLabel("I authorize sending these preview values to this collector.")
    .check();
  await preview
    .getByRole("button", { name: "Render without inference" })
    .click();
  await expect(preview.getByRole("status")).toContainText("Rendered only");
  await expect(
    page.getByText(/Second <script>window.__promptXss=true/),
  ).toBeVisible();
  expect(
    await page.evaluate(() => Reflect.get(window, "__promptXss")),
  ).toBeUndefined();
  // Delay a real authorized response, then change selection. Earlier content must
  // never appear under a different selected version or survive a cleared view.
  let resume!: () => void;
  let entered!: () => void;
  const waiting = new Promise<void>((resolve) => {
    resume = resolve;
  });
  const began = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const routePattern = "**/prompts/" + prompt.id + "/versions/*/content";
  await page.route(routePattern, async (route) => {
    const response = await route.fetch();
    entered();
    await waiting;
    await route.fulfill({ response });
  });
  const read = page.getByRole("form", { name: "Purpose-bound prompt read" });
  await read.getByRole("button", { name: "Read selected prompt" }).click();
  await began;
  await page.getByRole("button", { name: "Select v1", exact: true }).click();
  resume();
  await expect(read.getByRole("status")).toContainText("discarded");
  await expect(
    page.getByRole("button", { name: "Clear prompt content" }),
  ).toHaveCount(0);
  await page.unroute(routePattern);
  await page.getByRole("button", { name: "Select v2", exact: true }).click();
  await page
    .getByText("Compare in a budgeted experiment", { exact: true })
    .click();
  const playground = page.getByRole("form", { name: "Prompt playground plan" });
  await playground.getByLabel("Comparison dataset").selectOption(dataset);
  await playground.getByLabel("Baseline prompt version").selectOption(version1);
  await playground.getByLabel("Minimum evaluated cases").fill("3");
  await playground
    .getByLabel(
      "I authorize placing these fixed variables and prompt versions in an encrypted experiment plan.",
    )
    .check();
  await playground
    .getByRole("button", { name: "Prepare frozen comparison" })
    .click();
  await expect(playground.getByRole("status")).toContainText(
    "No inference performed",
  );
  await expect(promotion.getByLabel("Expected alias revision")).toHaveValue(
    "1",
  );
  await expect(
    promotion.getByLabel("Expected current version ID (empty for new alias)"),
  ).toHaveValue(version1);
  await promotion
    .getByLabel("Alias approval reference")
    .fill("owned-second-alias");
  await promotion
    .getByRole("button", { name: "Promote exact version" })
    .click();
  await expect(promotion.getByRole("status")).toContainText("Alias changed");
  await page.getByRole("button", { name: "History for staging" }).click();
  const rollback = page.getByRole("form", { name: "Rollback revision 2" });
  await rollback
    .getByLabel("Rollback approval reference")
    .fill("owned-rollback");
  await rollback
    .getByRole("button", { name: "Restore exact previous version" })
    .click();
  await expect(page.getByText(/revision 3 · version/)).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
});
