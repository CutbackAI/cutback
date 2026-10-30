import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("current runtime policy protects native delivery and preserves charged-denial evidence", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_GUARDRAIL_BROWSER !== "true",
    "Requires owned checker, collector and gateway",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Runtime guardrails", exact: true })
    .click();
  const id = process.env.CUTBACK_GUARDRAIL_POLICY!;
  await page
    .getByRole("button", {
      name: "Review policy " + id.slice(0, 8),
      exact: true,
    })
    .click();
  const policy = page.getByRole("region", { name: "Selected runtime policy" });
  await expect(policy).toContainText("owned-record-task");
  await expect(
    policy.getByRole("button", { name: "Approve this exact runtime policy" }),
  ).toBeDisabled();
  await policy
    .getByRole("button", { name: "Read private policy for review" })
    .click();
  await expect(page.getByLabel("Private runtime policy")).toContainText(
    "SYNTHETIC_DENIED_LITERAL_OWNED",
  );
  await policy.getByRole("button", { name: "Hide private policy" }).click();
  await expect(page.getByLabel("Private runtime policy")).toHaveCount(0);
  await page
    .getByLabel(
      "I reviewed the private rules, scope, expiry, shared check limit and unavailable-check behavior.",
    )
    .check();
  await policy
    .getByRole("button", { name: "Approve this exact runtime policy" })
    .click();
  await expect(
    policy.getByRole("button", { name: "Approve this exact runtime policy" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Application budgets", exact: true })
    .click();
  await page.getByLabel("Runtime input and output policy").selectOption(id);
  await page
    .getByRole("button", { name: "Prepare gateway policy", exact: true })
    .click();
  const gatewayPolicy = page.getByRole("region", {
    name: "Reviewed gateway policy",
    exact: true,
  });
  await expect(gatewayPolicy).toContainText("PENDING");
  await page
    .getByRole("button", { name: "Approve this exact gateway policy" })
    .click();
  await expect(gatewayPolicy).toContainText("APPROVED");
  await page
    .getByRole("button", { name: "Create gateway key", exact: true })
    .click();
  const token = await page
    .getByLabel("Gateway key displayed once")
    .textContent();
  expect(Boolean(token?.startsWith("cbg_"))).toBe(true);
  await page.getByRole("button", { name: "Hide gateway key" }).click();
  const endpoint = process.env.CUTBACK_GATEWAY_UI_URL! + "/v1/responses";
  for (const expected of [200, 403]) {
    const response = await page.request.post(endpoint, {
      headers: {
        Authorization: "Bearer " + token,
        "X-Cutback-Operation-ID": crypto.randomUUID(),
        "X-Cutback-Observation-Delegate": "gateway-native-text-v1",
      },
      data: {
        model: "gpt-6-luna",
        input: "Owned synthetic question",
        store: false,
        stream: false,
        service_tier: "default",
        max_output_tokens: 128,
        reasoning: { effort: "medium" },
      },
    });
    expect(response.status()).toBe(expected);
    expect(
      (await response.text()).includes("SYNTHETIC_DENIED_LITERAL_OWNED"),
    ).toBe(false);
  }
  await page
    .getByRole("button", { name: "Runtime guardrails", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Review policy " + id.slice(0, 8),
      exact: true,
    })
    .click();
  await expect(policy).toContainText("blocked: deny");
  await expect(policy).toContainText("allowed: allow");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  const privateInStorage = await page.evaluate(
    (secret) =>
      [localStorage, sessionStorage].some((store) =>
        Object.values(store).some(
          (value) =>
            value.includes(secret) ||
            value.includes("SYNTHETIC_DENIED_LITERAL_OWNED"),
        ),
      ),
    token!,
  );
  expect(privateInStorage).toBe(false);
  const viewer = await browser.newContext();
  try {
    const read = await viewer.newPage();
    await read.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await read
      .getByLabel("Control API key")
      .fill(process.env.CUTBACK_GUARDRAIL_VIEWER!);
    await read
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await read
      .getByRole("button", { name: "Runtime guardrails", exact: true })
      .click();
    await read
      .getByRole("button", {
        name: "Review policy " + id.slice(0, 8),
        exact: true,
      })
      .click();
    await expect(
      read.getByRole("region", { name: "Selected runtime policy" }),
    ).toContainText("blocked: deny");
    await expect(
      read.getByRole("button", { name: "Read private policy for review" }),
    ).toHaveCount(0);
    await expect(
      read.getByRole("button", { name: "Disable this runtime policy" }),
    ).toHaveCount(0);
    await expect(
      read.getByText("SYNTHETIC_DENIED_LITERAL_OWNED", { exact: true }),
    ).toHaveCount(0);
  } finally {
    await viewer.close();
  }
  expect(errors).toEqual([]);
});
