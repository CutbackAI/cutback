import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("the observed workload guide opens real fixture confirmation on desktop and mobile", async ({
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_WORKLOAD_DEMO_BROWSER !== "true",
    "Requires the owned complete workload demo",
  );
  test.setTimeout(120000);
  for (const [token, width, height] of [
    [process.env.CUTBACK_UI_KEY!, 1365, 900],
    [process.env.CUTBACK_PORTFOLIO_VIEWER!, 390, 844],
  ] as const) {
    const context = await browser.newContext({ viewport: { width, height } });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.name));
    await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await page.getByLabel("Control API key").fill(token);
    await page
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await page.getByRole("button", { name: "Sample", exact: true }).click();
    const guide = page.getByRole("region", {
      name: "Observed repeat-workload walkthrough",
    });
    await expect(guide).toContainText(
      "Eight SDK calls produced three potential reuses",
    );
    await expect(guide).toContainText("Runtime caching remains disabled");
    await guide
      .getByRole("button", {
        name: "Inspect the repeat-workload confirmation",
        exact: true,
      })
      .click();
    const economics = page.getByRole("region", {
      name: "Workload-guided trial economics",
    });
    await expect(economics).toBeVisible();
    await expect(economics).toContainText("Complete modeled call accounting");
    await expect(economics).toContainText("realized savings remain unknown");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(
      await page.evaluate(
        (credential) =>
          [localStorage, sessionStorage].some((storage) =>
            Object.values(storage).some((value) => value.includes(credential)),
          ),
        token,
      ),
    ).toBe(false);
    expect(errors).toEqual([]);
    await context.close();
  }
});
