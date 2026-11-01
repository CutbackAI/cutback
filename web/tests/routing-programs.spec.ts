import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", video: "off", trace: "off" });
test("one routing approval is scoped, private, cancellable and accessible", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.CUTBACK_ROUTING_PROGRAM_TEMPLATE,
    "Requires owned source-routing fixture",
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
    await target.getByRole("button", { name: "Routing", exact: true }).click();
  };
  await connect(page, process.env.CUTBACK_UI_KEY!);
  const panel = page.getByRole("region", {
    name: "Automatic routing programs",
    exact: true,
  });
  await panel.getByText("Prepare a routing program", { exact: true }).click();
  await panel
    .getByLabel("Source-bound training template")
    .selectOption(process.env.CUTBACK_ROUTING_PROGRAM_TEMPLATE!);
  await panel
    .getByLabel("Observed baseline task-cost snapshot")
    .selectOption(process.env.CUTBACK_ROUTING_PROGRAM_SNAPSHOT!);
  await panel.getByLabel("Whole program maximum USD").fill("100");
  await panel.getByLabel("Whole program maximum requests").fill("2000");
  await panel.getByLabel("Whole program maximum seconds").fill("300");
  await panel
    .getByLabel("Assumed savings per successful task, USD")
    .fill("0.01");
  await panel.getByLabel("Assumed future successful tasks").fill("100000");
  await panel
    .getByRole("button", { name: "Prepare whole routing budget", exact: true })
    .click();
  const selected = panel.getByRole("region", {
    name: "Selected routing program",
    exact: true,
  });
  await expect(selected.getByRole("status")).toContainText(
    "Review the whole budget",
  );
  await expect(selected).toContainText("Unknown");
  await expect(
    selected.getByRole("button", { name: "Approve whole routing program" }),
  ).toBeDisabled();
  await selected
    .getByLabel("Routing program content purpose")
    .fill("owned-browser-review");
  await selected
    .getByRole("button", { name: "Read private routing program", exact: true })
    .click();
  await expect(selected).toContainText("Classify the declared");
  await selected
    .getByLabel("Routing program approval reference")
    .fill("owned-browser-only");
  await selected.getByRole("checkbox").check();
  const approval = page.waitForResponse(
    (response) =>
      response.url().includes("/routing/programs/") &&
      response.url().endsWith("/approve"),
  );
  await selected
    .getByRole("button", { name: "Approve whole routing program", exact: true })
    .click();
  expect((await approval).status()).toBe(200);
  await expect(selected.getByRole("status")).toContainText(
    "Approved; waiting for worker",
  );
  await selected
    .getByRole("button", { name: "Cancel routing program", exact: true })
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
  expect(retained.includes(process.env.CUTBACK_UI_KEY!)).toBe(false);
  expect(retained.includes("Classify the declared")).toBe(false);
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    recordVideo: undefined,
  });
  try {
    const viewer = await context.newPage();
    await connect(viewer, process.env.CUTBACK_ROUTING_PROGRAM_VIEWER!);
    const readOnly = viewer.getByRole("region", {
      name: "Automatic routing programs",
      exact: true,
    });
    await readOnly
      .getByRole("button", { name: /^Inspect routing program / })
      .first()
      .click();
    await expect(
      readOnly.getByRole("button", {
        name: "Read private routing program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      readOnly.getByRole("button", {
        name: "Approve whole routing program",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(readOnly).not.toContainText("Classify the declared");
    expect(
      (await new AxeBuilder({ page: viewer }).analyze()).violations,
    ).toEqual([]);
  } finally {
    await context.close();
  }
  expect(failures).toEqual([]);
});
