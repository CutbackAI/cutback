import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", video: "off", trace: "off" });
test("one common policy approval preserves privacy, budgets and mobile access", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.CUTBACK_SOURCE_POLICY_TEMPLATE,
    "Requires scoped source-policy fixture",
  );
  test.setTimeout(90000);
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  const connect = async (target: typeof page, key: string) => {
    await target.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
    await target.getByLabel("Control API key").fill(key);
    await target
      .getByRole("button", { name: "Connect to this collector", exact: true })
      .click();
    await target
      .getByRole("button", { name: "Automatic improvements", exact: true })
      .click();
  };
  await connect(page, process.env.CUTBACK_UI_KEY!);
  const panel = page.getByRole("region", {
    name: "Automatic source policy programs",
    exact: true,
  });
  await panel
    .getByText("Prepare a source policy program", { exact: true })
    .click();
  await panel
    .getByLabel("Source-bound comparison template")
    .selectOption(process.env.CUTBACK_SOURCE_POLICY_TEMPLATE!);
  await panel
    .getByLabel("Observed baseline task-cost snapshot")
    .selectOption(process.env.CUTBACK_SOURCE_POLICY_SNAPSHOT!);
  await panel.getByLabel("Whole program maximum USD").fill("100");
  await panel.getByLabel("Whole program maximum requests").fill("1000");
  await panel.getByLabel("Whole program maximum seconds").fill("600");
  await panel
    .getByLabel("Assumed savings per successful task, USD")
    .fill("0.01");
  await panel.getByLabel("Assumed future successful tasks").fill("100000");
  await panel
    .getByRole("button", {
      name: "Prepare whole source policy budget",
      exact: true,
    })
    .click();
  const selected = panel.getByRole("region", {
    name: "Selected source policy program",
    exact: true,
  });
  await expect(selected.getByRole("status")).toContainText(
    "Review the whole budget",
  );
  await expect(selected).toContainText("Unknown");
  await expect(selected).toContainText(
    "Modeled provider cost and declared cache upper bounds",
  );
  await expect(selected).toContainText("not verified invoices");
  await expect(selected).not.toContainText("OWNED_PRIVATE_POLICY_INSTRUCTION");
  await expect(
    selected.getByRole("button", {
      name: "Approve whole source policy program",
    }),
  ).toBeDisabled();
  await selected
    .getByLabel("Source policy program content purpose")
    .fill("owned-common-policy-browser");
  await selected
    .getByRole("button", {
      name: "Read private source policy program",
      exact: true,
    })
    .click();
  await expect(selected).toContainText("OWNED_PRIVATE_POLICY_INSTRUCTION");
  await selected
    .getByLabel("Source policy program approval reference")
    .fill("owned-browser-budget");
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
    recordVideo: undefined,
  });
  try {
    const viewer = await context.newPage();
    await connect(viewer, process.env.CUTBACK_SOURCE_POLICY_VIEWER!);
    const readOnly = viewer.getByRole("region", {
      name: "Automatic source policy programs",
      exact: true,
    });
    await readOnly
      .getByRole("button", { name: /^Inspect source policy program / })
      .first()
      .click();
    await expect(
      readOnly.getByRole("button", {
        name: "Read private source policy program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      readOnly.getByRole("button", {
        name: "Approve whole source policy program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(readOnly).not.toContainText(
      "OWNED_PRIVATE_POLICY_INSTRUCTION",
    );
    expect(
      (await new AxeBuilder({ page: viewer }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await context.close();
  }
  expect(failures).toEqual([]);
});
