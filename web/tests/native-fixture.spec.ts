import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("fixture evidence stays explicit for administrators and mobile viewers", async ({
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_NATIVE_FIXTURE_BROWSER !== "true",
    "Requires the owned native fixture pipeline",
  );
  test.setTimeout(90000);
  for (const [credential, width, height, viewer] of [
    [process.env.CUTBACK_UI_KEY!, 1365, 900, false],
    [process.env.CUTBACK_FIXTURE_VIEWER!, 390, 844, true],
  ] as const) {
    const context = await browser.newContext({ viewport: { width, height } });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.name));
    await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await page.getByLabel("Control API key").fill(credential);
    await page
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page
      .getByRole("button", { name: "View reproducible evidence", exact: true })
      .click();
    const notice = page.getByRole("status", {
      name: "Synthetic native execution",
      exact: true,
    });
    await expect(notice).toContainText("Local fixture responses and usage");
    await expect(notice).toContainText("does not verify real model quality");
    if (viewer)
      await expect(page.getByLabel("Maximum experiment USD")).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(
      (await new AxeBuilder({ page }).analyze()).violations.map(
        (item) => item.id,
      ),
    ).toEqual([]);
    expect(errors).toEqual([]);
    await context.close();
  }
});
