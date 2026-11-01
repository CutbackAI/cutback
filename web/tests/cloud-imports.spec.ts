import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";

const profiles = [
  {
    profile: "bedrock-automated-model-jsonl.v1",
    file: "bedrock-automated-model.jsonl",
    rows: 1,
  },
  {
    profile: "azure-ai-evaluation-1.18.10-results.v1",
    file: "azure-ai-evaluation-1.18.10-results.json",
    rows: 2,
  },
  {
    profile: "vertexai-1.153.1-metrics-jsonl.v1",
    file: "vertexai-1.153.1-metrics.jsonl",
    rows: 2,
  },
  {
    profile: "vertexai-1.153.1-metrics-jsonl.v1",
    file: "vertexai-1.153.1-missing.jsonl",
    rows: 1,
  },
];

for (const entry of profiles) {
  test(`purpose-scoped cloud import: ${entry.file}`, async ({ page }) => {
    const project = process.env.CUTBACK_UI_PROJECT!;
    const token = process.env.CUTBACK_UI_KEY!;
    const policy = await page.request.post(
      `/v1/projects/${project}/capture-policies`,
      {
        headers: { Authorization: "Bearer " + token },
        data: {
          enabled: true,
          approval_ref: "owned-cloud-browser",
          rights_basis: "synthetic",
          environments: ["staging"],
          workflows: ["cloud-import"],
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
    await page.getByLabel("Control API key").fill(token);
    await page
      .getByRole("button", { name: "Connect to this collector" })
      .click();
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page.getByText("Import a qualified export", { exact: true }).click();
    const form = page.getByRole("form", {
      name: "Upload an encrypted import preview",
    });
    await form.getByLabel("Export format").selectOption(entry.profile);
    let raw = readFileSync(`../tests/fixtures/imports/${entry.file}`);
    const literalMetric =
      'Owned metric <img src="x" onerror="window.syntheticBad=true">';
    if (entry.profile.startsWith("bedrock")) {
      const source = JSON.parse(raw.toString());
      source.automatedEvaluationResult.scores[0].metricName = literalMetric;
      raw = Buffer.from(JSON.stringify(source));
    }
    await form.getByLabel("Qualified export file").setInputFiles({
      name: entry.file,
      mimeType: "application/json",
      buffer: raw,
    });
    await form
      .getByRole("combobox", { name: /Import rights policy/ })
      .selectOption(policyID);
    await form
      .getByLabel("Import workflow", { exact: true })
      .fill("cloud-import");
    await form
      .getByLabel("Import provenance reference")
      .fill("owned-cloud-qualification");
    await form
      .getByLabel(
        "I authorize uploading this export under the selected rights policy.",
      )
      .check();
    await form
      .getByRole("button", { name: "Upload for encrypted preview" })
      .click();
    await expect(form.getByRole("status")).toContainText(
      `${entry.rows} compatible rows, 0 rejections. Dataset not created.`,
    );
    const metrics = page.getByRole("region", {
      name: "Source-reported metric values",
    });
    await expect(metrics).toHaveCount(0);
    await page
      .getByLabel("Import content read purpose")
      .fill("owned-cloud-mapping-review");
    await page.getByRole("button", { name: "Read mapping examples" }).click();
    await expect(metrics).toBeVisible();
    await expect(metrics).toContainText("no common scale");
    await expect(page.locator(".import-examples pre")).toHaveCount(entry.rows);
    if (entry.file.includes("missing")) {
      await expect(metrics.getByText("missing", { exact: true })).toBeVisible();
      await expect(metrics.getByText("unknown", { exact: true })).toBeVisible();
    } else if (entry.profile.startsWith("azure")) {
      await expect(metrics.getByText("true", { exact: true })).toBeVisible();
      await expect(metrics.getByText("false", { exact: true })).toBeVisible();
    } else if (entry.profile.startsWith("bedrock")) {
      await expect(
        metrics.getByText(literalMetric, { exact: true }),
      ).toBeVisible();
      expect(await page.locator('img[src="x"]').count()).toBe(0);
      expect(
        await page.evaluate(() => (window as any).syntheticBad),
      ).toBeUndefined();
    }
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.getByRole("button", { name: "Hide import examples" }).click();
    await expect(metrics).toHaveCount(0);
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
    await commit
      .getByRole("button", { name: "Import reviewed mapping" })
      .click();
    await expect(
      page
        .getByRole("status")
        .filter({ hasText: "Unreviewed dataset created:" }),
    ).toBeVisible();
    await expect(metrics).toHaveCount(0);
    expect(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
    ).toBe(0);
  });
}
