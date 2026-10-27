import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("authenticated session to failing step, delayed scores, mobile and accessible waterfall", async ({
  page,
}) => {
  page.on("pageerror", (error) => {
    throw error;
  });
  await page.goto(
    "/dashboard/?project=" +
      encodeURIComponent(process.env.CUTBACK_UI_PROJECT!) +
      "&view=Traces",
  );
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  await page.getByRole("button", { name: "Traces", exact: true }).click();
  await expect(
    page.getByRole("table", { name: /Retained traces/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Browse sessions" }).click();
  await page.getByRole("button", { name: /Open session synthetic-/ }).click();
  await expect(
    page.getByRole("heading", { name: /Session: synthetic-/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Open trace/ }).click();
  await expect(
    page.getByRole("heading", { name: "Trace investigation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: /Trace tree and waterfall/ }),
  ).toBeVisible();
  await expect(page.getByText("lookup.order", { exact: true })).toBeVisible();
  await page.getByLabel("Execution status").selectOption("all");
  await page.getByText("Authorized encrypted content", { exact: true }).click();
  await page
    .getByLabel("Content read purpose")
    .fill("synthetic-browser-review");
  await page
    .getByRole("button", { name: "Read encrypted span content" })
    .click();
  await expect(page.locator(".trace-investigation pre")).toContainText(
    "<script>synthetic</script>",
  );
  await expect(page.locator(".trace-investigation script")).toHaveCount(0);
  await page.getByRole("button", { name: "Hide span content" }).click();
  await expect(page.locator(".trace-investigation pre")).toHaveCount(0);
  await expect(
    page.getByText(/4 retained spans · 1 observed execution errors/),
  ).toBeVisible();
  await expect(page.getByText("order-state.correct / 1")).toBeVisible();
  await expect(
    page.getByText(/Full trace cost and capture coverage are unknown/),
  ).toBeVisible();
  await page.getByLabel("Execution status").selectOption("error");
  await expect(
    page
      .getByRole("table", { name: /Trace tree and waterfall/ })
      .getByRole("row"),
  ).toHaveCount(2);
  await expect(page.getByText("lookup.order", { exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Trace investigation" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  await page.getByText("Filter retained traces", { exact: true }).click();
  await page
    .getByLabel("Workflow identifier", { exact: true })
    .fill("absent-workflow");
  await page.getByRole("button", { name: "Apply trace filters" }).click();
  await expect(
    page.getByText(/No retained traces for this session/),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear trace filters" }).click();
  await expect(
    page.getByRole("table", { name: /Retained traces/ }),
  ).toBeVisible();
});

test("trace loading, empty, error and retry states never show stale evidence", async ({
  page,
}) => {
  await page.goto(
    "/dashboard/?project=" +
      encodeURIComponent(process.env.CUTBACK_UI_PROJECT!),
  );
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page.getByRole("button", { name: "Connect to this collector" }).click();
  let mode = "loading";
  await page.route("**/v1/projects/*/traces?*", async (route) => {
    if (mode === "loading") {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({ json: { items: [], next_cursor: null } });
    } else if (mode === "error")
      await route.fulfill({
        status: 503,
        json: { code: "STORAGE_UNAVAILABLE" },
      });
    else await route.continue();
  });
  await page.getByRole("button", { name: "Traces", exact: true }).click();
  await expect(page.getByText("Loading workflow evidence…")).toBeVisible();
  await expect(
    page.getByText(/No retained traces in this environment/),
  ).toBeVisible();
  mode = "error";
  await page.getByRole("button", { name: "Refresh investigation" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Could not load workflow evidence",
  );
  await expect(
    page.getByRole("table", { name: /Retained traces/ }),
  ).toHaveCount(0);
  mode = "real";
  await page.getByRole("button", { name: "Retry evidence" }).click();
  await expect(
    page.getByRole("table", { name: /Retained traces/ }),
  ).toBeVisible();
  await page.route("**/v1/projects/*/traces/*", (route) =>
    route.fulfill({ status: 404, json: { code: "TRACE_NOT_FOUND" } }),
  );
  await page.getByRole("button", { name: /Open trace/ }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Could not load this trace",
  );
  await expect(
    page.getByRole("table", { name: /Trace tree and waterfall/ }),
  ).toHaveCount(0);
});
