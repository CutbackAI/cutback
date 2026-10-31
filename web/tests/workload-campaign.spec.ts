import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("measured cache campaign, bounded preparation and mobile viewer", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_WORKLOAD_CAMPAIGN !== "true",
    "Requires the owned workload campaign",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Campaigns", exact: true }).click();
  await page
    .getByRole("button", {
      name: new RegExp(
        "Inspect .*" + process.env.CUTBACK_CAMPAIGN_ID!.slice(0, 8) + "$",
      ),
    })
    .click();
  const selected = page.getByRole("region", { name: "Selected campaign" });
  await expect(selected.getByRole("status")).toContainText("COMPLETED");
  await expect(selected).toContainText("Instruction generation was skipped");
  await expect(selected).toContainText("synthetic confirmation");
  await expect(
    selected.getByRole("region", { name: "Frozen static portfolio" }),
  ).toContainText("Exact cache");
  await expect(selected).toContainText("never used as a generation seed");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByText("Prepare a bounded search", { exact: true }).click();
  await page
    .getByLabel("Prepared application plan")
    .selectOption(process.env.CUTBACK_CAMPAIGN_TEMPLATE!);
  await page
    .getByLabel("Frozen baseline cost window")
    .selectOption(process.env.CUTBACK_CAMPAIGN_SNAPSHOT!);
  await page
    .getByLabel("Conservative savings per successful task (USD)")
    .fill("0.1");
  await page.getByLabel("Forecast successful tasks").fill("1000");
  await page.getByLabel("Total request ceiling").fill("1500");
  await page
    .getByRole("button", {
      name: "Prepare campaign without inference",
      exact: true,
    })
    .click();
  await expect(selected.getByRole("status")).toContainText(
    "READY_FOR_APPROVAL",
  );
  await expect(selected).toContainText("retained source window");
  await expect(
    selected.getByRole("button", { name: "Approve and queue this campaign" }),
  ).toBeDisabled();
  await selected
    .getByRole("button", { name: "Cancel new campaign calls" })
    .click();
  await expect(selected.getByRole("status")).toContainText("CANCELLED");
  await expect(selected.getByRole("status")).toContainText(
    "0 request reservations",
  );
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  try {
    const mobile = await context.newPage();
    await mobile.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await mobile
      .getByLabel("Control API key")
      .fill(process.env.CUTBACK_WORKLOAD_CAMPAIGN_VIEWER!);
    await mobile
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await mobile
      .getByRole("button", { name: "Campaigns", exact: true })
      .click();
    await mobile
      .getByRole("button", {
        name: new RegExp(
          "Inspect .*" + process.env.CUTBACK_CAMPAIGN_ID!.slice(0, 8) + "$",
        ),
      })
      .click();
    await expect(
      mobile.getByRole("region", { name: "Selected campaign" }),
    ).toContainText("Exact cache");
    await expect(
      mobile.getByText("Prepare a bounded search", { exact: true }),
    ).toHaveCount(0);
    await expect(
      mobile.getByRole("button", { name: "Approve and queue this campaign" }),
    ).toHaveCount(0);
    expect(
      await mobile.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    expect(
      (await new AxeBuilder({ page: mobile }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await context.close();
  }
  expect(errors).toEqual([]);
});
