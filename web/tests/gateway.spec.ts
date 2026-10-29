import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

// Never save a failure screenshot while a one-time credential may be visible.
test.use({ screenshot: "off", trace: "off", video: "off" });

test("finite application budgets preserve uncertain money and one-shot native operations", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_GATEWAY_BROWSER !== "true",
    "Requires owned synthetic gateway and collector",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Application budgets", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Application budgets",
      level: 1,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Prepare gateway policy", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("Native text streaming")).toHaveValue(
    "disabled",
  );
  await page.getByLabel("Native text streaming").selectOption("enabled");
  await page
    .getByRole("button", { name: "Prepare gateway policy", exact: true })
    .click();
  const review = page.getByRole("region", {
    name: "Reviewed gateway policy",
    exact: true,
  });
  await expect(review).toContainText("PENDING");
  await expect(review).toContainText("HTTP SSE and nonstreaming text");
  await expect(review).toContainText("reasoning medium");
  await expect(review).toContainText("requested tier default");
  await page
    .getByRole("button", { name: "Approve this exact gateway policy" })
    .click();
  await expect(review).toContainText("APPROVED");
  await page
    .getByRole("button", { name: "Create gateway key", exact: true })
    .click();
  const credential = page.getByRole("region", {
    name: "New gateway credential",
  });
  await expect(credential).toBeVisible();
  const token = await page
    .getByLabel("Gateway key displayed once")
    .textContent();
  expect(Boolean(token?.startsWith("cbg_"))).toBe(true);
  await page.getByRole("button", { name: "Hide gateway key" }).click();
  await expect(credential).toHaveCount(0);
  const storageContainsCredential = await page.evaluate(
    (value) =>
      [localStorage, sessionStorage].some((storage) =>
        Object.values(storage).some((item) => item.includes(value)),
      ),
    token!,
  );
  expect(storageContainsCredential).toBe(false);
  const operation = crypto.randomUUID();
  const options = {
    headers: {
      Authorization: "Bearer " + token,
      "X-Cutback-Operation-ID": operation,
      "X-Cutback-Observation-Delegate": "gateway-native-text-v1",
    },
    data: {
      model: "gpt-6-luna",
      input: "owned browser synthetic private phrase",
      max_output_tokens: 128,
      store: false,
      service_tier: "default",
      reasoning: { effort: "medium" },
      stream: true,
    },
  };
  const gateway = process.env.CUTBACK_GATEWAY_UI_URL! + "/v1/responses";
  const first = await page.request.post(gateway, options);
  expect(first.status()).toBe(200);
  expect(first.headers()["content-type"]).toContain("text/event-stream");
  expect(await first.text()).toContain("response.completed");
  expect(first.headers()["x-should-retry"]).toBe("false");
  expect((await page.request.post(gateway, options)).status()).toBe(409);
  const uncertain = await page.request.post(gateway, {
    ...options,
    headers: {
      ...options.headers,
      "X-Cutback-Operation-ID": crypto.randomUUID(),
    },
  });
  expect(uncertain.status()).toBe(200);
  await page.getByRole("button", { name: "Refresh budget records" }).click();
  const policies = page.getByRole("region", {
    name: "Gateway policies",
    exact: true,
  });
  await policies
    .getByRole("button", { name: /^Inspect policy / })
    .first()
    .click();
  await expect(review).toContainText("1 uncertain");
  await expect(review).toContainText("1.051536");
  const receipts = page.getByRole("region", {
    name: "Gateway call records",
    exact: true,
  });
  await expect(receipts).toContainText("SETTLED");
  await expect(receipts).toContainText("UNCERTAIN");
  await expect(receipts).toContainText("Unknown");
  await expect(receipts).toContainText("HTTP SSE");
  await expect(
    receipts.getByRole("columnheader", { name: "Gateway first text (ms)" }),
  ).toBeVisible();
  const exported = await page.request.get(
    `/v1/projects/${process.env.CUTBACK_UI_PROJECT}/export`,
    {
      headers: { Authorization: "Bearer " + process.env.CUTBACK_UI_KEY },
    },
  );
  expect(exported.status()).toBe(200);
  const metadata = await exported.text();
  expect(metadata.includes(token!)).toBe(false);
  expect(metadata.includes(options.data.input)).toBe(false);
  expect(metadata.includes('"request_hmac"')).toBe(false);
  expect(metadata.includes('"digest"')).toBe(false);
  await page
    .getByRole("button", { name: /^Revoke key / })
    .first()
    .click();
  expect(
    (
      await page.request.post(gateway, {
        ...options,
        headers: {
          ...options.headers,
          "X-Cutback-Operation-ID": crypto.randomUUID(),
        },
      })
    ).status(),
  ).toBe(401);
  await page
    .getByRole("button", { name: "Disable policy for new calls" })
    .click();
  await expect(review).toContainText("DISABLED");
  await page
    .getByRole("button", { name: "Resume with retained limits", exact: true })
    .click();
  await expect(review).toContainText("APPROVED");
  await expect(review).toContainText("1 uncertain");
  await page
    .getByRole("button", { name: "Disable policy for new calls" })
    .click();
  await expect(review).toContainText("DISABLED");
  await page
    .getByRole("button", { name: "Resume with retained limits", exact: true })
    .click();
  await expect(review).toContainText("APPROVED");
  await expect(review).toContainText("1 uncertain");
  await page
    .getByRole("button", { name: "Disable policy for new calls" })
    .click();
  await expect(review).toContainText("DISABLED");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await review.screenshot({ path: "../artifacts/gateway-budgets-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await review.screenshot({ path: "../artifacts/gateway-budgets-mobile.png" });
  const viewerContext = await browser.newContext();
  try {
    const viewer = await viewerContext.newPage();
    await viewer.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await viewer
      .getByLabel("Control API key")
      .fill(process.env.CUTBACK_GATEWAY_VIEWER!);
    await viewer
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await viewer
      .getByRole("button", { name: "Application budgets", exact: true })
      .click();
    await expect(
      viewer.getByRole("region", { name: "Gateway policies", exact: true }),
    ).toContainText("DISABLED");
    await expect(
      viewer.getByRole("button", {
        name: "Prepare gateway policy",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      viewer.getByRole("button", { name: /^Revoke key / }),
    ).toHaveCount(0);
    await expect(
      viewer.getByRole("region", { name: "New gateway credential" }),
    ).toHaveCount(0);
    expect(
      (await new AxeBuilder({ page: viewer }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await viewerContext.close();
  }
  expect(errors).toEqual([]);
});
