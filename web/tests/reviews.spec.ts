import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("independent browser sessions complete blind disagreement, adjudication and a new frozen dataset", async ({
  page,
  browser,
}) => {
  const key = process.env.CUTBACK_UI_KEY!;
  const auth = { Authorization: "Bearer " + key };
  const created = await page.request.post("/v1/projects", {
    headers: auth,
    data: { slug: "browser-review-" + Date.now(), environment: "staging" },
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()).id;
  const base = "/v1/projects/" + project;
  const policyResponse = await page.request.post(base + "/capture-policies", {
    headers: auth,
    data: {
      enabled: true,
      approval_ref: "synthetic-browser-review",
      rights_basis: "synthetic",
      environments: ["staging"],
      workflows: ["review-classify"],
      allowed_providers: ["local"],
      sample_rate: 1,
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      max_samples: 100,
      max_payload_bytes: 64000,
    },
  });
  const policy = (await policyResponse.json()).id;
  const datasetResponse = await page.request.post(base + "/datasets", {
    headers: auth,
    data: {
      policy_id: policy,
      workflow: "review-classify",
      environment: "staging",
      source_type: "synthetic_fixture",
      independent_labels: false,
      representative: false,
      provenance_ref: "synthetic-browser-source",
      cases: [
        {
          id: "orchid",
          input: "Classify a synthetic orchid request.",
          gold: null,
          reference: "unverified historical answer",
        },
      ],
    },
  });
  expect(datasetResponse.status()).toBe(201);
  const dataset = (await datasetResponse.json()).id;
  const credentials = [];
  for (let i = 0; i < 2; i++) {
    const response = await page.request.post(base + "/keys", {
      headers: auth,
      data: { role: "analyst", environment: "staging" },
    });
    expect(response.status()).toBe(201);
    credentials.push(await response.json());
  }
  async function connect(p: Page, token: string) {
    await p.goto("/dashboard/?project=" + project);
    await p.getByLabel("Control API key").fill(token);
    await p.getByRole("button", { name: "Connect to this collector" }).click();
    await p.getByRole("button", { name: "Review", exact: true }).click();
    await expect(
      p.getByRole("heading", { name: "Review queues", exact: true }),
    ).toBeVisible();
  }
  await connect(page, key);
  await page.getByText("Create a frozen review queue", { exact: true }).click();
  const source = page.getByRole("form", {
    name: "Choose review source dataset",
  });
  await source
    .getByRole("combobox", { name: "Review source dataset" })
    .selectOption(dataset);
  await source.getByRole("button", { name: "List source examples" }).click();
  await page.getByRole("checkbox", { name: /Example 1/ }).check();
  const freeze = page.getByRole("form", {
    name: "Freeze rubric and assignments",
  });
  await freeze
    .getByRole("combobox", { name: "Review rights policy" })
    .selectOption(policy);
  await freeze
    .getByLabel("Frozen review rubric")
    .fill(
      "Label the synthetic orchid request A or B. Independently assess it.",
    );
  for (const c of credentials)
    await freeze
      .getByRole("checkbox", {
        name: "Assign analyst key " + c.key_id.slice(0, 8),
      })
      .check();
  await freeze
    .getByLabel("Review provenance reference")
    .fill("synthetic-browser-review");
  await freeze
    .getByLabel(
      "I authorize these sources and the encrypted review copies under this policy.",
    )
    .check();
  await freeze
    .getByRole("button", { name: "Create encrypted review queue" })
    .click();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Frozen review queue created." }),
  ).toBeVisible();
  const origin = new URL(page.url()).origin;
  const contexts = [];
  try {
    for (let i = 0; i < credentials.length; i++) {
      const context = await browser.newContext({ baseURL: origin });
      contexts.push(context);
      const reviewer = await context.newPage();
      await connect(reviewer, credentials[i].token);
      await reviewer.getByRole("button", { name: /Open review / }).click();
      await reviewer
        .getByRole("button", { name: "Claim next blind review" })
        .click();
      await reviewer
        .getByLabel("Review read purpose")
        .fill("synthetic-blind-review");
      await reviewer
        .getByRole("button", { name: "Read my review example" })
        .click();
      await expect(
        reviewer.getByRole("region", { name: "Authorized review content" }),
      ).toContainText("Classify a synthetic orchid request.");
      await expect(
        reviewer.getByRole("heading", { name: "Completed blind answers" }),
      ).toHaveCount(0);
      const vote = reviewer.getByRole("form", {
        name: "Submit immutable review",
      });
      await vote.getByLabel("Gold label as JSON").fill(i === 0 ? '"A"' : '"B"');
      await vote
        .getByLabel("Review rationale")
        .fill("Independent synthetic review.");
      expect(
        (await new AxeBuilder({ page: reviewer }).analyze()).violations,
      ).toEqual([]);
      await reviewer.setViewportSize({ width: 390, height: 844 });
      expect(
        await reviewer.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      expect(
        (await new AxeBuilder({ page: reviewer }).analyze()).violations,
      ).toEqual([]);
      await vote.getByRole("button", { name: "Submit my judgment" }).click();
      await expect(
        reviewer.getByRole("form", { name: "Submit immutable review" }),
      ).toHaveCount(0);
      expect(
        await reviewer.evaluate(
          () => localStorage.length + sessionStorage.length,
        ),
      ).toBe(0);
    }
    await page.getByRole("button", { name: /Open review / }).click();
    await page
      .getByLabel("Adjudication read purpose")
      .fill("synthetic-disagreement-review");
    await page
      .getByRole("button", { name: "Read as assigned adjudicator" })
      .click();
    await expect(
      page.getByRole("heading", { name: "Completed blind answers" }),
    ).toBeVisible();
    const resolve = page.getByRole("form", {
      name: "Resolve the disagreement",
    });
    await resolve.getByLabel("Gold label as JSON").fill('"A"');
    await resolve
      .getByLabel("Review rationale")
      .fill("Resolved using the frozen synthetic rubric.");
    await resolve
      .getByRole("button", { name: "Save immutable adjudication" })
      .click();
    await expect(
      page.getByRole("region", { name: "Authorized adjudication" }),
    ).toHaveCount(0);
    const promotion = page.getByRole("form", { name: "Promote reviewed data" });
    await promotion
      .getByLabel("Reviewed dataset provenance")
      .fill("synthetic-reviewed-browser-v1");
    await promotion
      .getByLabel(
        "I confirm these reviewed inputs and labels are authorized under the queue policy.",
      )
      .check();
    await promotion
      .getByRole("button", { name: "Create frozen reviewed dataset" })
      .click();
    await expect(promotion.getByRole("status")).toContainText(
      "Frozen reviewed dataset created:",
    );
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    const rows = await (
      await page.request.get(base + "/datasets", { headers: auth })
    ).json();
    expect(rows.length).toBe(2);
    expect(
      rows.find((d: any) => d.id !== dataset).provenance.independent_labels,
    ).toBe(false);
  } finally {
    for (const context of contexts) await context.close();
  }
});
