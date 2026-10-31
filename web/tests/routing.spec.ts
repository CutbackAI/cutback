import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("routing training, private fitting, independent validation and confirmation", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_ROUTING_BROWSER !== "true",
    "Requires owned synthetic worker and PostgreSQL",
  );
  test.setTimeout(240000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.name));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Routing", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Learn input-size routing" }),
  ).toBeVisible();
  if (process.env.CUTBACK_ROUTING_INSPECTION) {
    await page
      .getByText("1. Prepare a training comparison", { exact: true })
      .click();
    const upload = page.getByLabel(
      "Qualified application inspection (optional JSON)",
      { exact: true },
    );
    await upload.setInputFiles({
      name: "bad.json",
      mimeType: "application/json",
      buffer: Buffer.from("{broken"),
    });
    await expect(page.getByRole("alert")).toContainText(
      "File contents omitted",
    );
    await upload.setInputFiles(process.env.CUTBACK_ROUTING_INSPECTION);
    await expect(
      page
        .getByRole("region", {
          name: "Learned input-size routing",
          exact: true,
        })
        .getByRole("status"),
    ).toContainText("Application bound: app.py");
    await expect(
      page.getByRole("combobox", { name: "Routing provider", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByLabel("Shared task instruction", { exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", {
        name: "Clear application inspection",
        exact: true,
      })
      .click();
    await expect(
      page.getByLabel("Shared task instruction", { exact: true }),
    ).toBeVisible();
    await upload.setInputFiles(process.env.CUTBACK_ROUTING_INSPECTION);
    const form = page.getByRole("form", {
      name: "Prepare routing training",
      exact: true,
    });
    await form
      .getByLabel("Required confirmation samples", { exact: true })
      .fill("3");
    await form
      .getByLabel("Maximum USD for this stage", { exact: true })
      .fill("2");
    await form
      .getByLabel("Maximum requests for this stage", { exact: true })
      .fill("500");
    const prepared = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/routing/training-drafts") &&
        response.status() === 201,
    );
    await form
      .getByRole("button", { name: "Freeze training plan", exact: true })
      .click();
    const response = await prepared;
    expect(response.request().postDataJSON().source.producer_file).toBe(
      "app.py",
    );
    await expect(
      page.getByRole("form", {
        name: "Review exact routing plan",
        exact: true,
      }),
    ).toBeVisible();
    await page
      .getByText("1. Prepare a training comparison", { exact: true })
      .click();
  } else
    await page
      .getByRole("button", { name: /^Inspect training plan / })
      .first()
      .click();

  async function approve() {
    await page
      .getByLabel("Routing plan read purpose", { exact: true })
      .fill("owned-browser-exact-review");
    await page
      .getByRole("button", { name: "Read exact routing plan", exact: true })
      .click();
    await page
      .getByLabel("Routing stage approval reference", { exact: true })
      .fill("owned-synthetic-budget");
    await page
      .getByRole("button", { name: "Approve this routing budget", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Queue this approved routing stage",
        exact: true,
      }),
    ).toBeVisible();
  }
  async function completed() {
    await expect(async () => {
      await page
        .getByRole("button", { name: "Refresh routing execution", exact: true })
        .click();
      await expect(
        page.getByRole("region", { name: "Routing run evidence", exact: true }),
      ).toContainText("COMPLETED");
    }).toPass({ timeout: 60000, intervals: [1000, 2000, 5000] });
  }
  await approve();
  let queues = 0;
  let signal!: () => void;
  const lost = new Promise<void>((resolve) => {
    signal = resolve;
  });
  const queuePath = `**/v1/projects/${process.env.CUTBACK_UI_PROJECT}/experiments`;
  await page.route(queuePath, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    queues++;
    await route.fetch();
    await route.abort("connectionfailed");
    signal();
  });
  await page
    .getByRole("button", {
      name: "Queue this approved routing stage",
      exact: true,
    })
    .click();
  await lost;
  await page.unroute(queuePath);
  await completed();
  expect(queues).toBe(1);
  await expect(
    page.getByRole("button", {
      name: "Queue this approved routing stage",
      exact: true,
    }),
  ).toHaveCount(0);
  const fitting = page.getByRole("form", {
    name: "Fit routing from completed training",
    exact: true,
  });
  await expect(
    fitting.getByLabel("Completed routing training run").locator("option"),
  ).toHaveCount(1);
  await fitting.getByLabel("Minimum cases in each fitted leaf").fill("3");
  await fitting
    .getByLabel("Routing fit purpose reference")
    .fill("owned-browser-fit");
  await fitting
    .getByRole("button", { name: "Fit routing policy", exact: true })
    .click();
  await page
    .getByLabel("Fitted policy read purpose", { exact: true })
    .fill("owned-browser-fit-review");
  await page
    .getByRole("button", { name: "Read fitted policy", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Fitted route bounds", exact: true }),
  ).toContainText("gpt-4.1-mini");
  const validation = page.getByRole("form", {
    name: "Prepare independent routing validation",
    exact: true,
  });
  await validation.getByLabel("Maximum USD for this stage").fill("2");
  await validation.getByLabel("Maximum requests for this stage").fill("500");
  const validationResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/validation-drafts") &&
      response.request().method() === "POST",
  );
  await validation
    .getByLabel("Routing validation purpose")
    .fill("owned-browser-validation");
  await validation
    .getByRole("button", { name: "Freeze routing validation", exact: true })
    .click();
  expect((await validationResponse).status()).toBe(201);
  await approve();
  await page
    .getByRole("button", {
      name: "Queue this approved routing stage",
      exact: true,
    })
    .click();
  await completed();
  const confirmation = page.getByRole("form", {
    name: "Prepare untouched routing confirmation",
    exact: true,
  });
  await expect(
    confirmation
      .getByLabel("Completed routing validation run")
      .locator("option"),
  ).toHaveCount(1);
  await confirmation.getByLabel("Maximum USD for this stage").fill("2");
  await confirmation.getByLabel("Maximum requests for this stage").fill("500");
  let preparationAttempts = 0;
  let preparedId = "",
    preparedRequest = "";
  const confirmationPath = "**/routing/fits/*/confirmation-drafts";
  await page.route(confirmationPath, async (route) => {
    preparationAttempts++;
    const body = route.request().postDataJSON();
    if (preparationAttempts === 1) {
      preparedRequest = body.request_id;
      const committed = await route.fetch();
      expect(committed.status()).toBe(201);
      preparedId = (await committed.json()).id;
      return route.fulfill({
        status: 503,
        json: { code: "STORAGE_UNAVAILABLE" },
      });
    }
    expect(body.request_id).toBe(preparedRequest);
    return route.continue();
  });
  const confirmationResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/confirmation-drafts") &&
      response.request().method() === "POST" &&
      response.status() === 201,
  );
  await confirmation
    .getByLabel("Routing confirmation purpose")
    .fill("owned-browser-confirmation");
  await confirmation
    .getByRole("button", { name: "Freeze routing confirmation", exact: true })
    .click();
  const recovered = await confirmationResponse;
  expect((await recovered.json()).id).toBe(preparedId);
  expect(preparationAttempts).toBe(2);
  await page.unroute(confirmationPath);
  await approve();
  await page
    .getByRole("button", {
      name: "Queue this approved routing stage",
      exact: true,
    })
    .click();
  await completed();
  const evidence = page.getByRole("region", {
    name: "Routing run evidence",
    exact: true,
  });
  await expect(evidence).toContainText("Fresh held-out groups recorded");
  await expect(evidence).toContainText("Fixture responses and usage");
  await expect(evidence).toContainText("Verified at completion");
  await expect(evidence).toContainText("Count once using setup key");
  await expect(evidence).toContainText("Unknown");
  if (process.env.CUTBACK_ROUTING_INSPECTION) {
    await page
      .getByLabel("Routing source proposal purpose", { exact: true })
      .fill("owned-browser-source-review");
    await page
      .getByRole("button", {
        name: "Read application routing proposal",
        exact: true,
      })
      .click();
    const sourceProposal = page.getByRole("region", {
      name: "Application routing proposal",
      exact: true,
    });
    await expect(sourceProposal).toContainText("synthetic_local_review_only");
    const pendingDownload = page.waitForEvent("download");
    await page
      .getByRole("button", {
        name: "Download private routing proposal",
        exact: true,
      })
      .click();
    expect((await pendingDownload).suggestedFilename()).toBe(
      "routing-source-proposal.json",
    );
    await page
      .getByRole("button", {
        name: "Clear private application proposal",
        exact: true,
      })
      .click();
    await expect(
      sourceProposal.getByRole("button", {
        name: "Download private routing proposal",
        exact: true,
      }),
    ).toHaveCount(0);
  }
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(() =>
      [localStorage, sessionStorage].some((storage) =>
        Object.values(storage).some(
          (value) =>
            value.includes("Bearer ") ||
            value.includes("Classify the declared ticket"),
        ),
      ),
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const view = await viewer.newPage();
  await view.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await view
    .getByLabel("Control API key")
    .fill(process.env.CUTBACK_ROUTING_VIEWER!);
  await view
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await view.getByRole("button", { name: "Routing", exact: true }).click();
  await expect(
    view.getByRole("heading", { name: "Learn input-size routing" }),
  ).toBeVisible();
  await expect(
    view.getByRole("button", { name: "Fit routing policy", exact: true }),
  ).toHaveCount(0);
  await expect(
    view.getByRole("button", { name: "Read fitted policy", exact: true }),
  ).toHaveCount(0);
  await expect(
    view.getByLabel("Qualified application inspection (optional JSON)", {
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    view.getByRole("button", {
      name: "Read application routing proposal",
      exact: true,
    }),
  ).toHaveCount(0);
  await view
    .getByRole("button", { name: /^Read test result / })
    .first()
    .click();
  await expect(
    view.getByRole("region", { name: "Routing run evidence", exact: true }),
  ).toContainText("Fresh held-out groups recorded");
  expect((await new AxeBuilder({ page: view }).analyze()).violations).toEqual(
    [],
  );
  await viewer.close();
});
