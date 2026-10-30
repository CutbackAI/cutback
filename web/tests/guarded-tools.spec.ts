import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("guarded tool effects and reply release remain separate for administrators and viewers", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_GUARDED_TOOL_RUN,
    "Requires actual owned guarded Docker sample",
  );
  for (const key of [
    process.env.CUTBACK_UI_KEY!,
    process.env.CUTBACK_UI_VIEWER!,
  ]) {
    await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await page.getByLabel("Control API key").fill(key);
    await page
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page
      .getByRole("button", { name: "View reproducible evidence", exact: true })
      .click();
    const panel = page.getByRole("region", {
      name: "Isolated tool receipts",
      exact: true,
    });
    await panel
      .getByRole("button", { name: "Load isolated tool receipts", exact: true })
      .click();
    await expect(
      panel.getByRole("cell", { name: /Cleanup confirmed/ }),
    ).toHaveCount(4);
    await panel
      .getByRole("button", { name: /Inspect operations for session/ })
      .first()
      .click();
    const table = panel.getByRole("table", { name: /Tool operations for/ });
    await expect(table.locator("tbody tr")).toHaveCount(2);
    const patch = table
      .locator("tbody tr")
      .filter({ hasText: "records_patch" });
    await expect(patch).toContainText("RETURNED");
    await expect(patch).toContainText("Recorded");
    await expect(patch).toContainText("STARTED");
    await expect(patch).toContainText("Withheld; recorded effects remain");
    await expect(table).toContainText(
      "Release authorized; consumption unverified",
    );
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    }
    const retained = await page.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    );
    expect(retained).not.toContain(key);
    expect(page.url()).not.toContain(key);
    expect(await page.locator("body").innerText()).not.toContain(
      "SYNTHETIC_GUARDED_TOOL_CANARY",
    );
    if (key === process.env.CUTBACK_UI_KEY) {
      await page.setViewportSize({ width: 1280, height: 900 });
      await page.getByText("Plan an experiment", { exact: true }).click();
      const form = page.getByRole("form", {
        name: "Compare authorized alternatives",
        exact: true,
      });
      await form
        .getByRole("combobox", { name: "Frozen dataset", exact: true })
        .selectOption(process.env.CUTBACK_TOOL_DATASET!);
      await form
        .getByLabel("Tool service ID", { exact: true })
        .fill(process.env.CUTBACK_TOOL_BROKER!);
      await form
        .getByLabel("Authorized record owner ID", { exact: true })
        .fill(process.env.CUTBACK_TOOL_PRINCIPAL!);
      await form
        .getByLabel("Approved container image digest", { exact: true })
        .fill(process.env.CUTBACK_TEST_TOOL_IMAGE_ID!);
      await form
        .getByLabel(
          "I authorize the selected records to be used in this isolated tool service.",
          { exact: true },
        )
        .check();
      await form
        .getByLabel(
          "Require approved checks before each tool call and before releasing its response.",
          { exact: true },
        )
        .check();
      await form
        .getByLabel("Approved tool guard policy ID", { exact: true })
        .fill(process.env.CUTBACK_TOOL_GUARD_POLICY!);
      const draft = page.waitForResponse(
        (response) =>
          response.url().endsWith("/experiments/draft") &&
          response.request().method() === "POST",
      );
      await form
        .getByRole("button", { name: "Freeze proposed plan", exact: true })
        .click();
      const frozen = await draft;
      expect(frozen.status()).toBe(201);
      expect(frozen.request().postDataJSON().isolated_tools.version).toBe(
        "isolated-record-guarded-tools-v1",
      );
      const result = await frozen.json();
      expect(result.evaluator_version).toBe("deterministic-v10");
      expect(result.paid_calls_made).toBe(0);
      expect(result.approval_required).toBe(true);
      expect(
        frozen.request().postDataJSON().isolated_tools.runtime_guardrails
          .policy_id,
      ).toBe(process.env.CUTBACK_TOOL_GUARD_POLICY);
    }
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  }
});
