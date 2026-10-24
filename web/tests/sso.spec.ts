import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("real popup PKCE exchange with isolated-opener IdP keeps access tokens in memory", async ({
  page,
  context,
}) => {
  test.skip(
    process.env.CUTBACK_UI_SSO_TEST !== "true",
    "Requires the local RSA/JWKS/PKCE identity fixture.",
  );
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/dashboard/");
  await expect(
    page.getByRole("button", { name: "Sign in with organization SSO" }),
  ).toBeVisible();
  const popupPromise = context.waitForEvent("page");
  await page
    .getByRole("button", { name: "Sign in with organization SSO" })
    .click();
  const popup = await popupPromise;
  await popup
    .getByRole("button", { name: "Continue synthetic identity login" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Security", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Teams", exact: true }),
  ).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  expect(await context.cookies()).toEqual([]);
  expect(page.url()).not.toContain("code=");
  expect(page.url()).not.toContain("token");
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: /Understand your AI spend/ }),
  ).toBeVisible();
  await expect(page.getByLabel("Control API key")).toHaveValue("");
  expect(failures).toEqual([]);
  if (!popup.isClosed()) await popup.close();
});
