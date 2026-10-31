import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", video: "off", trace: "off" });

test("approved request reuse evidence is scoped, private and accessible", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.CUTBACK_PROFILE_POLICY,
    "Requires owned encrypted profile fixture",
  );
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  const connect = async (target: typeof page, key: string) => {
    await target.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await target.getByLabel("Control API key").fill(key);
    await target
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await target
      .getByRole("button", { name: "Request reuse", exact: true })
      .click();
  };
  await connect(page, process.env.CUTBACK_UI_KEY!);
  const panel = page.getByRole("region", {
    name: "Request reuse analysis",
    exact: true,
  });
  await panel
    .getByLabel("Approved request-profile policy")
    .selectOption(process.env.CUTBACK_PROFILE_POLICY!);
  await panel.getByLabel("Profile workflow").fill("classify");
  await panel
    .getByLabel("Window start (ISO with UTC offset)")
    .fill(process.env.CUTBACK_PROFILE_START!);
  await panel
    .getByLabel("Window end (ISO with UTC offset)")
    .fill(process.env.CUTBACK_PROFILE_END!);
  await panel.getByRole("button", { name: "Queue bounded analysis" }).click();
  await expect
    .poll(async () => {
      await panel.getByRole("button", { name: "Refresh analyses" }).click();
      return panel.textContent();
    })
    .toContain("Repeat evidence supports a separate cache trial");
  const receipt = panel.getByRole("article");
  await expect(receipt).toContainText("COMPLETED");
  await expect(receipt).toContainText(
    "Infrastructure cost, semantic quality and realized savings",
  );
  await expect(receipt).toContainText("Unknown");
  await receipt
    .getByText("Check this window against your application", { exact: true })
    .click();
  const upload = receipt.getByLabel(
    "Source workload input (private metadata JSON)",
  );
  await upload.setInputFiles({
    name: "bad.json",
    mimeType: "application/json",
    buffer: Buffer.from("{broken"),
  });
  await expect(receipt.getByRole("alert")).toContainText(
    "File contents omitted",
  );
  await upload.setInputFiles(process.env.CUTBACK_PROFILE_WORKLOAD_INPUT!);
  const assessed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/cache-trial") &&
      response.request().method() === "POST",
  );
  await receipt
    .getByRole("button", { name: "Assess source-matched trial eligibility" })
    .click();
  const assessedResponse = await assessed;
  expect(assessedResponse.status()).toBe(200);
  const assessment = await assessedResponse.json();
  expect(assessment.provider_calls).toBe(0);
  expect(assessment.encrypted_storage_reads).toBe(0);
  expect(assessment.paid_calls_authorized).toBe(false);
  expect(assessment.source_change_authorized).toBe(false);
  await expect(
    receipt.getByRole("status", { name: "Source workload assessment" }),
  ).toContainText(
    "This source window supports preparing a separate cache trial",
  );
  await expect(page.locator("body")).not.toContainText(
    process.env.CUTBACK_PROFILE_PRIVATE_FINGERPRINT!,
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  const retained = await page.evaluate(() =>
    [localStorage, sessionStorage]
      .map((store) => Object.values(store).join(" "))
      .join(" "),
  );
  expect(retained.includes(process.env.CUTBACK_UI_KEY!)).toBe(false);
  expect(
    retained.includes(process.env.CUTBACK_PROFILE_PRIVATE_FINGERPRINT!),
  ).toBe(false);
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    recordVideo: undefined,
  });
  try {
    const viewer = await context.newPage();
    await connect(viewer, process.env.CUTBACK_PROFILE_VIEWER!);
    await expect(
      viewer.getByText(
        "Project administrator access is required to authorize encrypted request analysis.",
      ),
    ).toBeVisible();
    await expect(
      viewer.getByRole("button", { name: "Queue bounded analysis" }),
    ).toHaveCount(0);
  } finally {
    await context.close();
  }
  expect(failures).toEqual([]);
});
