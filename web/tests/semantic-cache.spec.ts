import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("frozen cache quality gates and activation stay scoped with no retained credentials", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_SEMANTIC_NAMESPACE,
    "Requires the owned vector-control sample",
  );
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  const key = process.env.CUTBACK_UI_KEY!;
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(key);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Semantic caching", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Semantic cache qualification",
    exact: true,
  });
  await panel
    .getByRole("combobox", { name: "Semantic namespace", exact: true })
    .selectOption(process.env.CUTBACK_SEMANTIC_NAMESPACE!);
  const workspace = panel.getByRole("region", {
    name: `Semantic namespace ${process.env.CUTBACK_SEMANTIC_NAMESPACE!.slice(0, 8)}`,
    exact: true,
  });
  const receipt = workspace.getByRole("region", {
    name: `Cache qualification ${process.env.CUTBACK_SEMANTIC_QUALIFICATION!.slice(0, 8)}`,
    exact: true,
  });
  await expect(receipt).toContainText("PASSED");
  await expect(receipt).toContainText("Selected threshold: 0.99");
  await expect(receipt).toContainText(
    "Complete task cost and realized savings: unknown",
  );
  await expect(receipt).toContainText("False-hit groups / negative controls");
  await workspace
    .getByText("Curated catalog metadata and authorized review", {
      exact: true,
    })
    .click();
  const entry = workspace.getByRole("combobox", {
    name: "Catalog entry to review",
    exact: true,
  });
  const entryId = await entry.locator("option").nth(1).getAttribute("value");
  await entry.selectOption(entryId!);
  await workspace
    .getByLabel("Catalog content review purpose", { exact: true })
    .fill("owned-browser-curation-review");
  await workspace
    .getByRole("button", {
      name: "Read purpose-authorized catalog content",
      exact: true,
    })
    .click();
  await expect(
    workspace
      .locator("pre")
      .filter({ hasText: "Owned curated billing training question" }),
  ).toBeVisible();
  await workspace
    .getByRole("button", {
      name: "Clear reviewed catalog content",
      exact: true,
    })
    .click();
  await expect(
    workspace
      .locator("pre")
      .filter({ hasText: "Owned curated billing training question" }),
  ).toHaveCount(0);
  await workspace
    .getByLabel("Semantic dataset JSON file", { exact: true })
    .setInputFiles(process.env.CUTBACK_SEMANTIC_UPLOAD_FILE!);
  await expect(
    workspace.getByRole("button", {
      name: "Upload and seal dataset",
      exact: true,
    }),
  ).toBeDisabled();
  await workspace
    .getByLabel(
      "I authorize uploading these queries, labels, vectors and fallback outputs to the approved encrypted store.",
      { exact: true },
    )
    .check();
  const sealed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/finalize") &&
      response.request().method() === "POST",
  );
  await workspace
    .getByRole("button", { name: "Upload and seal dataset", exact: true })
    .click();
  expect((await sealed).status()).toBe(202);
  await expect(
    workspace.getByRole("region", {
      name: "Encrypted dataset upload",
      exact: true,
    }),
  ).toContainText("FINALIZING: 16 cases");
  await receipt
    .getByRole("button", { name: "Review activation eligibility", exact: true })
    .click();
  await expect(receipt).toContainText("Passing evidence is currently eligible");
  await expect(
    receipt.getByRole("button", {
      name: "Activate reviewed semantic cache",
      exact: true,
    }),
  ).toBeDisabled();
  await receipt
    .getByLabel(/Cache approval reference/)
    .fill("owned-browser-activation");
  const activation = page.waitForResponse(
    (response) =>
      response.url().endsWith("/activate") &&
      response.request().method() === "POST",
  );
  await receipt
    .getByRole("button", {
      name: "Activate reviewed semantic cache",
      exact: true,
    })
    .click();
  const activated = await activation;
  expect(activated.status()).toBe(200);
  expect(activated.request().postDataJSON().approval_hash).toMatch(
    /^[a-f0-9]{64}$/,
  );
  await expect(workspace.getByRole("heading", { level: 3 })).toContainText(
    "ACTIVE",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
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
    `**/projects/${process.env.CUTBACK_UI_PROJECT}/semantic-cache/namespaces?*`,
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
      .getByRole("button", { name: "Refresh semantic caches", exact: true })
      .click();
    await started;
    await page
      .getByLabel("Project", { exact: true })
      .selectOption(process.env.CUTBACK_SEMANTIC_OTHER_PROJECT!);
    await expect(panel).toContainText("No semantic namespaces on this page");
    release();
    await finished;
    await page.unrouteAll({ behavior: "wait" });
    await expect(
      panel.getByRole("region", {
        name: `Semantic namespace ${process.env.CUTBACK_SEMANTIC_NAMESPACE!.slice(0, 8)}`,
        exact: true,
      }),
    ).toHaveCount(0);
    expect(failures).toEqual([]);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});
