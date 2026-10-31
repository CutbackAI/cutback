import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("workload trial costs and synthetic evidence remain scoped on desktop and mobile", async ({
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_WORKLOAD_PORTFOLIO_BROWSER !== "true",
    "Requires the owned source/workload/native-cache pipeline",
  );
  test.setTimeout(90000);
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
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page
      .getByRole("button", { name: "View reproducible evidence", exact: true })
      .first()
      .click();
    const economics = page.getByRole("region", {
      name: "Workload-guided trial economics",
      exact: true,
    });
    await expect(economics).toBeVisible();
    await expect(economics).toContainText(
      "Validation and confirmation known subtotal",
    );
    await expect(economics).toContainText("Complete modeled call accounting");
    await expect(economics).toContainText("Rejected trials are included");
    await expect(economics).toContainText("realized savings remain unknown");
    await expect(
      page.getByRole("status", {
        name: "Synthetic execution disclosure",
        exact: true,
      }),
    ).toContainText("not verified");
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
