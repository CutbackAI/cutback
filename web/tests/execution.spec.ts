import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("synthetic agent and retrieval evidence, per-call accounting, authorized trajectories and mobile", async ({
  page,
}) => {
  test.skip(
    process.env.CUTBACK_SUPPORT_DEMO !== "true",
    "Requires isolated PostgreSQL demo",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let releaseInitial = () => {};
  let initialDelivered = () => {};
  let initialStarted = () => {};
  let held = false;
  const hold = new Promise<void>((resolve) => {
    releaseInitial = resolve;
  });
  const delivered = new Promise<void>((resolve) => {
    initialDelivered = resolve;
  });
  const started = new Promise<void>((resolve) => {
    initialStarted = resolve;
  });
  await page.route(
    /\/v1\/projects\/[a-f0-9-]+\/experiments\/[a-f0-9-]+$/,
    async (route) => {
      if (held) {
        await route.continue();
        return;
      }
      held = true;
      const response = await route.fetch();
      initialStarted();
      await hold;
      await route.fulfill({ response });
      initialDelivered();
    },
  );
  await page.goto("/dashboard/");
  await page
    .getByRole("button", { name: "Explore sample", exact: true })
    .click();
  await started;
  await page
    .getByRole("button", {
      name: "Inspect agent and retrieval tasks",
      exact: true,
    })
    .click();
  await page
    .getByRole("button", { name: "Load execution receipts", exact: true })
    .click();
  const receipts = page.getByRole("region", {
    name: "Agent execution and call receipts",
  });
  await expect(
    receipts
      .getByRole("table", { name: "Reserved execution operations" })
      .locator("tbody tr"),
  ).toHaveCount(12);
  releaseInitial();
  await delivered;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    receipts
      .getByRole("table", { name: "Reserved execution operations" })
      .locator("tbody tr"),
  ).toHaveCount(12);
  await expect(
    receipts
      .getByRole("table", { name: "Synthetic task episodes" })
      .locator("tbody tr"),
  ).toHaveCount(4);
  await expect(receipts).toContainText("12 reserved operations");
  await expect(
    page.getByRole("region", {
      name: "Semantic assessment for baseline",
      exact: true,
    }),
  ).toContainText("NOT VERIFIED");
  await expect(
    page.getByRole("region", {
      name: "Semantic assessment for baseline",
      exact: true,
    }),
  ).toContainText("2 judge calls");
  await expect(receipts).toContainText("100.0%");
  await page
    .getByLabel("Purpose for reading case evidence")
    .fill("owned-agent-browser");
  await page
    .getByRole("button", { name: "Read authorized case outputs", exact: true })
    .click();
  await expect(page.locator(".paired-case")).toHaveCount(2);
  await expect(
    page.getByRole("group", { name: "Semantic episode proxy" }).first(),
  ).toContainText("exact source quotes");
  await expect(
    page
      .getByRole("list", { name: "Conversation and tool trajectory" })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Declared task checks" }).first(),
  ).toBeVisible();
  await expect(page.locator(".paired-case").first()).toContainText(
    '"kind": "tool"',
  );
  await expect(page.locator(".paired-case").first()).toContainText(
    '"final_state"',
  );
  await page
    .locator(".paired-case")
    .first()
    .getByText("Review or revise this episode contract", { exact: true })
    .click();
  await page
    .getByLabel("Correction review reference")
    .first()
    .fill("owned-episode-contract-revision");
  await page
    .getByRole("button", { name: "Create corrected dataset", exact: true })
    .first()
    .click();
  await expect(
    page
      .getByText(/Created a new dataset. The original evidence is unchanged/)
      .first(),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: "../artifacts/agent-episodes-browser.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  await page.getByText("Plan an experiment", { exact: true }).click();
  const plan = page.getByRole("form", {
    name: "Compare authorized alternatives",
  });
  const choice = plan
    .getByLabel("Frozen dataset")
    .locator("option")
    .filter({ hasText: "synthetic.agent" })
    .first();
  await plan
    .getByLabel("Frozen dataset")
    .selectOption((await choice.getAttribute("value"))!);
  await plan.getByLabel("Comparison strategy").selectOption("prompt_variant");
  await plan
    .getByLabel("Reviewed candidate instruction")
    .fill("Use the declared fixtures and cite the supporting document.");
  await plan.getByLabel("Add a semantic model judge", { exact: true }).check();
  await plan
    .getByLabel("Semantic evaluation rubric 1", { exact: true })
    .fill("Assess agreement with the accepted reference.");
  await plan
    .getByRole("button", { name: "Add semantic criterion", exact: true })
    .click();
  await plan
    .getByRole("combobox", { name: "Semantic check 2", exact: true })
    .selectOption("conversation_constraints");
  await plan
    .getByLabel("Semantic evaluation rubric 2", { exact: true })
    .fill("Assess compliance with the observed user request.");
  await plan.getByLabel("Maximum experiment USD", { exact: true }).fill("10");
  await plan
    .getByRole("button", { name: "Freeze proposed plan", exact: true })
    .click();
  await expect(plan.getByRole("status")).toContainText("frozen.");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
});
