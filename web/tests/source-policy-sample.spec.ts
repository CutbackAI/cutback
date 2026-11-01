import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", video: "off", trace: "off" });
test("the independent sample explains rejected controls and reaches its recorded program", async ({
  page,
}) => {
  test.skip(
    !process.env.CUTBACK_SOURCE_POLICY_SAMPLE,
    "Requires the completed owned sample",
  );
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page
    .getByRole("button", { name: "Explore sample", exact: true })
    .click();
  const guide = page.getByRole("region", {
    name: "One-budget improvement walkthrough",
    exact: true,
  });
  await expect(guide.getByRole("listitem")).toHaveCount(4);
  await expect(guide).toContainText("reduced output limits fail too");
  await expect(guide).toContainText(
    "static_validation → training → routed_validation → confirmation",
  );
  await expect(guide).toContainText(process.env.CUTBACK_SOURCE_POLICY_SAMPLE!);
  await expect(guide).toContainText("Nothing has been deployed");
  await expect(
    page.getByRole("region", { name: "Sample experiment result", exact: true }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await guide
    .getByRole("button", {
      name: "Follow the one-budget improvement",
      exact: true,
    })
    .click();
  await page
    .getByRole("button", {
      name:
        "Inspect source policy program " +
        process.env.CUTBACK_SOURCE_POLICY_SAMPLE,
      exact: true,
    })
    .click();
  const selected = page.getByRole("region", {
    name: "Selected source policy program",
    exact: true,
  });
  await expect(selected.getByRole("status")).toContainText(
    "Evaluation complete",
  );
  for (const phase of [
    "static validation",
    "training",
    "routed validation",
    "confirmation",
  ]) {
    await expect(selected).toContainText(phase);
  }
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const stored = await page.evaluate(() =>
    [localStorage, sessionStorage]
      .map((storage) => Object.values(storage).join(" "))
      .join(" "),
  );
  expect(stored).not.toContain("source_directory");
  expect(stored).not.toContain(process.env.CUTBACK_UI_KEY!);
  expect(stored).not.toMatch(/Bearer|"token"|"api_key"/i);
  expect(failures).toEqual([]);
});
