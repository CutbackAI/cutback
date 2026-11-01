import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", video: "off", trace: "off" });
test("cached-current evidence, scoped approval, mobile and privacy", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.CUTBACK_CURRENT_POLICY_PROGRAM,
    "Requires a source-matched current policy",
  );
  const program = process.env.CUTBACK_CURRENT_POLICY_PROGRAM!;
  const active = process.env.CUTBACK_CURRENT_CACHE_ACTIVE === "true";
  const connect = async (target: typeof page, key: string) => {
    await target.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await target.getByLabel("Control API key").fill(key);
    await target
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await target
      .getByRole("button", { name: "Automatic improvements", exact: true })
      .click();
    await target
      .getByRole("button", {
        name: "Inspect source policy program " + program,
        exact: true,
      })
      .click();
    return target.getByRole("region", {
      name: "Selected source policy program",
      exact: true,
    });
  };
  const selected = await connect(page, process.env.CUTBACK_UI_KEY!);
  const observed = selected.getByRole("region", {
    name: "Observed current cache reference",
  });
  await expect(observed).toContainText(
    active ? "Active in the retained operations" : "Configured but inactive",
  );
  await expect(observed).toContainText("Capture coverage is unknown");
  await expect(observed).toContainText(
    active ? "Isolated cold-cache replay" : "Uncached replay",
  );
  await expect(observed).toContainText("without cache");
  await expect(selected).not.toContainText("OWNED_PRIVATE_POLICY_INSTRUCTION");
  await expect(selected).not.toContainText("summary_sha256");
  await selected
    .getByLabel("Source policy program content purpose")
    .fill("owned-current-browser");
  await selected
    .getByRole("button", {
      name: "Read private source policy program",
      exact: true,
    })
    .click();
  await expect(selected).toContainText("OWNED_PRIVATE_POLICY_INSTRUCTION");
  await selected
    .getByLabel("Source policy program approval reference")
    .fill("owned-current-approval");
  await selected.getByRole("checkbox").check();
  await selected
    .getByRole("button", {
      name: "Approve whole source policy program",
      exact: true,
    })
    .click();
  await expect(selected.getByRole("status")).toContainText(
    "Approved; waiting for worker",
  );
  await selected
    .getByRole("button", { name: "Cancel source policy program", exact: true })
    .click();
  await expect(selected.getByRole("status")).toContainText("Cancelled");
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
  expect(retained).not.toContain(process.env.CUTBACK_UI_KEY!);
  expect(retained).not.toContain("OWNED_PRIVATE_POLICY_INSTRUCTION");
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  try {
    const viewer = await context.newPage();
    const readonly = await connect(
      viewer,
      process.env.CUTBACK_SOURCE_POLICY_VIEWER!,
    );
    await expect(
      readonly.getByRole("region", {
        name: "Observed current cache reference",
      }),
    ).toBeVisible();
    await expect(
      readonly.getByRole("button", {
        name: "Read private source policy program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      readonly.getByRole("button", {
        name: "Approve whole source policy program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(readonly).not.toContainText(
      "OWNED_PRIVATE_POLICY_INSTRUCTION",
    );
    expect(
      (await new AxeBuilder({ page: viewer }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await context.close();
  }
});
