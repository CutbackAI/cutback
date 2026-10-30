import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("paired guard decisions, incomplete checks and benign interruption for admin and viewer", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_GUARDED_SECURITY_RUN,
    "Requires actual owned guarded security sample",
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
    const reports = page.getByRole("region", {
      name: /Runtime tool guard coverage for/,
    });
    await expect(reports).toHaveCount(2);
    for (const report of await reports.all()) {
      await expect(report).toContainText(
        "Some configured checks did not complete",
      );
      await expect(report).toContainText("Known for observed tool checks");
      await expect(report).toContainText(
        "Benign families interrupted by the guard: 100.0%",
      );
    }
    expect(await page.locator("body").innerText()).not.toContain(
      "owned-canary-boundary",
    );
    expect(await page.locator("body").innerText()).toContain(
      "General guardrail false-positive rate: Unknown",
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
    expect(
      await page.evaluate(() =>
        JSON.stringify({
          local: { ...localStorage },
          session: { ...sessionStorage },
        }),
      ),
    ).not.toContain(key);
    expect(page.url()).not.toContain(key);
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  }
});
