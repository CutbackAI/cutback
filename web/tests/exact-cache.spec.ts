import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("cache approvals and actual replay evidence remain scoped on desktop and mobile", async ({
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_EXACT_CACHE_BROWSER !== "true",
    "Requires the owned exact-cache pipeline",
  );
  test.setTimeout(90000);
  for (const [credential, width, height, viewer] of [
    [process.env.CUTBACK_UI_KEY!, 1365, 900, false],
    [process.env.CUTBACK_CACHE_VIEWER!, 390, 844, true],
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
    const approval = page.getByRole("region", {
      name: /^Exact cache contracts for plan /,
    });
    await expect(approval).toContainText("Explicitly authorized");
    await expect(approval).toContainText("not an invoice");
    await page
      .getByRole("button", { name: "View reproducible evidence", exact: true })
      .click();
    const evidence = page.getByRole("status", {
      name: "Exact cache trial",
      exact: true,
    });
    await expect(evidence).toContainText(
      "3 hits · 3 native SDK calls · 6 logical requests",
    );
    await expect(evidence).toContainText("Qualified for this replay");
    await expect(evidence).toContainText("not priced again");
    await expect(evidence).toContainText("remain unknown");
    await page
      .getByRole("button", { name: "Load execution receipts", exact: true })
      .click();
    const receipts = page.getByRole("region", {
      name: "Agent execution and call receipts",
      exact: true,
    });
    await expect(
      receipts.getByText("Historical model: gpt-4.1", { exact: true }),
    ).toHaveCount(3);
    await expect(
      receipts.getByText(
        /0 native SDK calls; historical usage is not priced again/,
      ),
    ).toHaveCount(3);
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
