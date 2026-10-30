import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("a newcomer sees a failed three-label task with scoped in-memory login and paired answers", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_TICKET_SAMPLE !== "true",
    "Requires the owned ticket-routing fixture",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dashboard/");
  const connection = page.waitForResponse(
    (response) =>
      response.url().endsWith("/v1/demo/connect") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Explore sample", exact: true })
    .click();
  const token = (await (await connection).json()).token;
  await expect(
    page.getByRole("heading", {
      name: "Three labels. One automatic improvement.",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Inspect ticket-routing failures",
      exact: true,
    })
    .click();
  const result = page.getByRole("region", { name: "Sample experiment result" });
  await expect(result).toContainText("Not eligible");
  const cases = page.getByRole("region", { name: "Encrypted case review" });
  await cases
    .getByLabel("Purpose for reading case evidence")
    .fill("owned-ticket-review");
  await cases
    .getByRole("button", { name: "Read authorized case outputs", exact: true })
    .click();
  const paired = cases.locator(".paired-case").first();
  await expect(paired).toBeVisible();
  await expect(paired.getByText(/^Expected:/)).toContainText(
    /access|technical/,
  );
  await expect(
    paired.getByText("billing", { exact: true }).first(),
  ).toBeVisible();
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
    }),
  );
  expect(storage.includes(token)).toBe(false);
  expect(page.url().includes(token)).toBe(false);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
});
