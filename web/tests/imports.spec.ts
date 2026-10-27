import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";

test("qualified producer export requires encrypted preview, mapping review and rights before import", async ({
  page,
}) => {
  const project = process.env.CUTBACK_UI_PROJECT!;
  const key = process.env.CUTBACK_UI_KEY!;
  const policy = await page.request.post(
    `/v1/projects/${project}/capture-policies`,
    {
      headers: { Authorization: "Bearer " + key },
      data: {
        enabled: true,
        approval_ref: "synthetic-browser-import",
        rights_basis: "synthetic",
        environments: ["staging"],
        workflows: ["import-classify"],
        allowed_providers: ["local"],
        sample_rate: 1,
        expires_at: new Date(Date.now() + 86400000).toISOString(),
        max_samples: 20,
        max_payload_bytes: 64000,
      },
    },
  );
  expect(policy.status()).toBe(201);
  const policyID = (await policy.json()).id;
  await page.goto("/dashboard/?project=" + encodeURIComponent(project));
  await page.getByLabel("Control API key").fill(key);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page.getByText("Import a qualified export", { exact: true }).click();
  const form = page.getByRole("form", {
    name: "Upload an encrypted import preview",
  });
  await form
    .getByLabel("Export format")
    .selectOption("promptfoo-0.125.1-results.v3");
  await form.getByLabel("Qualified export file").setInputFiles({
    name: "owned-synthetic-results.json",
    mimeType: "application/json",
    buffer: readFileSync("../tests/fixtures/imports/promptfoo-0.125.1-v3.json"),
  });
  await form
    .getByRole("combobox", { name: /Import rights policy/ })
    .selectOption(policyID);
  await form
    .getByLabel("Import workflow", { exact: true })
    .fill("import-classify");
  await form
    .getByLabel("Import provenance reference")
    .fill("synthetic-producer-qualification");
  await form
    .getByLabel(
      "I authorize uploading this export under the selected rights policy.",
    )
    .check();
  await form
    .getByRole("button", { name: "Upload for encrypted preview" })
    .click();
  await expect(form.getByRole("status")).toContainText(
    "2 compatible rows, 0 rejections. Dataset not created.",
  );
  await expect(
    page.getByRole("heading", { name: "Review the frozen mapping" }),
  ).toBeVisible();
  await expect(
    page.getByText("testCase.vars.input", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Import content read purpose")
    .fill("synthetic-mapping-acceptance");
  await page.getByRole("button", { name: "Read mapping examples" }).click();
  await expect(page.locator(".import-examples pre")).toHaveCount(2);
  await expect(page.locator(".import-examples pre").first()).toContainText(
    "declared_expectation_unreviewed",
  );
  const commit = page.getByRole("form", {
    name: "Create the unreviewed dataset",
  });
  await commit
    .getByLabel("I reviewed this exact source hash and mapping.")
    .check();
  await commit
    .getByLabel(
      "These projected cases are authorized for this policy and retention.",
    )
    .check();
  await commit.getByRole("button", { name: "Import reviewed mapping" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Unreviewed dataset created:" }),
  ).toBeVisible();
  await expect(page.locator(".import-examples")).toHaveCount(0);
  await expect(
    page.getByText(
      /Human review is required before claiming independent labels/,
    ),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
});
