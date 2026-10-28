import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("inspect canonical campaign evidence, approve exact bounds and cancel without inference", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_CAMPAIGN_SAMPLE !== "true",
    "Requires the owned native campaign fixture",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Campaigns", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Search within a fixed budget",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: new RegExp(
        "Inspect .*" + process.env.CUTBACK_CAMPAIGN_ID!.slice(0, 8) + "$",
      ),
    })
    .click();
  const selected = page.getByRole("region", { name: "Selected campaign" });
  await expect(selected.getByRole("status")).toContainText("COMPLETED");
  await expect(selected).toContainText(
    "The selected alternative passed synthetic confirmation.",
  );
  await expect(selected).toContainText("generated-r1-2");
  await expect(selected).toContainText("confirmation");
  await selected
    .getByText("Decision and evidence limits", { exact: true })
    .click();
  await expect(selected).toContainText(
    "synthetic_confirmation_passed_real_data_required",
  );
  await expect(page.getByText("Answer A only.", { exact: true })).toHaveCount(
    0,
  );
  await page
    .getByLabel("Purpose for reading a generated instruction")
    .fill("synthetic-candidate-review");
  await page
    .getByRole("button", {
      name: "Read generated-r1-2 instruction",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("region", { name: "Authorized candidate content" }),
  ).toContainText("Answer A only.");
  await page.getByText("Prepare a bounded search", { exact: true }).click();
  await page
    .getByLabel("Prepared application plan")
    .selectOption(process.env.CUTBACK_CAMPAIGN_TEMPLATE!);
  await page
    .getByLabel("Frozen baseline cost window")
    .selectOption(process.env.CUTBACK_CAMPAIGN_SNAPSHOT!);
  await page
    .getByLabel("Conservative savings per successful task (USD)")
    .fill("0.01");
  await page.getByLabel("Forecast successful tasks").fill("10000");
  await page
    .getByRole("button", {
      name: "Prepare campaign without inference",
      exact: true,
    })
    .click();
  await expect(selected.getByRole("status")).toContainText(
    "READY_FOR_APPROVAL",
  );
  const approve = page.getByRole("button", {
    name: "Approve and queue this campaign",
    exact: true,
  });
  await expect(approve).toBeDisabled();
  await page
    .getByLabel("Approval reference", { exact: true })
    .fill("synthetic-browser-only");
  await page
    .getByLabel(
      "I approve this exact campaign and its provider-call ceilings.",
      { exact: true },
    )
    .check();
  await approve.click();
  await expect(selected.getByRole("status")).toContainText("QUEUED");
  await expect(selected.getByRole("status")).toContainText(
    "0 request reservations",
  );
  await page
    .getByRole("button", { name: "Cancel new campaign calls", exact: true })
    .click();
  await expect(selected.getByRole("status")).toContainText("CANCELLED");
  await expect(selected.getByRole("status")).toContainText(
    "0 request reservations",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(selected).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/campaign-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
