import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.use({ screenshot: "off", trace: "off", video: "off" });

test("reviewed criteria use distinct budget, content, validation and confirmation approvals", async ({
  page,
  browser,
}) => {
  test.skip(
    process.env.CUTBACK_CRITERIA_BROWSER !== "true",
    "Requires owned native worker and PostgreSQL",
  );
  test.setTimeout(120000);
  const errors: string[] = [];
  const semantic = process.env.CUTBACK_CRITERIA_SEMANTIC === "true";
  page.on("pageerror", (e) => errors.push(e.message));
  const base = `/v1/projects/${process.env.CUTBACK_UI_PROJECT}`;
  const headers = { Authorization: `Bearer ${process.env.CUTBACK_UI_KEY}` };
  await page.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await page.getByLabel("Control API key").fill(process.env.CUTBACK_UI_KEY!);
  await page
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await page.getByRole("button", { name: "Experiments", exact: true }).click();
  const region = page.getByRole("region", {
    name: "Reviewed evaluation criteria",
    exact: true,
  });
  await region
    .getByRole("button", {
      name: "Inspect criteria " + process.env.CUTBACK_CRITERIA_ID,
      exact: true,
    })
    .click();
  const selected = region.getByRole("region", {
    name: "Selected criteria version",
  });
  await selected
    .getByLabel("Generation approval reference")
    .fill("owned-browser-generation-budget");
  await selected
    .getByLabel(
      "I authorize this one provider call under the current capture policy and budget.",
    )
    .check();
  await selected
    .getByRole("button", { name: "Approve exact generation plan", exact: true })
    .click();
  await selected.getByLabel("Run this exact approved generation plan.").check();
  await selected
    .getByRole("button", { name: "Queue generation once", exact: true })
    .click();
  await expect
    .poll(async () => {
      await region
        .getByRole("button", { name: "Refresh criteria", exact: true })
        .click();
      return selected.textContent();
    })
    .toContain("COMPLETED");
  await expect(
    selected.getByRole("heading", { name: "Proposed checks" }),
  ).toHaveCount(0);
  await selected
    .getByLabel("Criteria content read purpose")
    .fill("owned-browser-private-review");
  await selected
    .getByRole("button", { name: "Read authorized content", exact: true })
    .click();
  await expect(selected).toContainText(
    semantic
      ? "Review the reference against the returned answer"
      : "Compare category and priority",
  );
  await selected
    .getByRole("combobox", { name: "Criteria decision", exact: true })
    .selectOption("approve");
  await selected
    .getByLabel("Criteria review reference")
    .fill("owned-browser-criteria-review");
  await selected
    .getByLabel(
      "I reviewed the examples, metric meaning and fixed quality requirements. This is not independent judge calibration.",
    )
    .check();
  await selected
    .getByRole("button", { name: "Record criteria decision", exact: true })
    .click();
  await expect(
    selected.getByRole("heading", { name: "Proposed checks" }),
  ).toHaveCount(0);
  await selected
    .getByRole("combobox", { name: "Validation dataset", exact: true })
    .selectOption(process.env.CUTBACK_CRITERIA_DATASET!);
  await selected
    .getByLabel("Validation budget USD", { exact: true })
    .fill("10");
  if (semantic)
    await selected
      .getByLabel("Maximum native turns per episode", { exact: true })
      .fill("1");
  await selected
    .getByRole("button", { name: "Freeze validation plan", exact: true })
    .click();
  const validationNotice = selected
    .getByRole("status")
    .filter({ hasText: "Validation plan" });
  await expect(validationNotice).toContainText("frozen");

  async function executePlan(id: string) {
    const plans = await page.request.get(base + "/experiments/plans", {
      headers,
    });
    const plan = (await plans.json()).find((p: any) => p.id === id);
    expect(Boolean(plan)).toBe(true);
    const approval = await page.request.post(
      base + `/experiments/plans/${id}/approve`,
      {
        headers,
        data: {
          plan_hash: plan.specification?.plan_hash ?? plan.plan_hash,
          approval_ref: "owned-browser-target-budget",
        },
      },
    );
    expect(approval.status()).toBe(200);
    const queued = await page.request.post(base + "/experiments", {
      headers,
      data: { plan_id: id },
    });
    expect(queued.status()).toBe(202);
    const runId = (await queued.json()).id;
    let result: any;
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            base + "/experiments/" + runId,
            { headers },
          );
          result = await response.json();
          return result.status;
        },
        { timeout: 45000 },
      )
      .toBe("COMPLETED");
    return { id: runId, result };
  }
  const validationId = (await validationNotice.textContent())!.match(
    /[a-f0-9-]{36}/,
  )![0];
  const validation = await executePlan(validationId);
  await region
    .getByRole("button", { name: "Refresh criteria", exact: true })
    .click();
  await selected
    .getByRole("combobox", { name: "Completed validation run", exact: true })
    .selectOption(validation.id);
  await selected
    .getByLabel("Confirmation budget USD", { exact: true })
    .fill("10");
  await selected
    .getByRole("button", {
      name: "Freeze untouched-test confirmation",
      exact: true,
    })
    .click();
  const confirmationNotice = selected
    .getByRole("status")
    .filter({ hasText: "Confirmation plan" });
  await expect(confirmationNotice).toContainText("frozen");
  const confirmationId = (await confirmationNotice.textContent())!.match(
    /[a-f0-9-]{36}/,
  )![0];
  const confirmed = await executePlan(confirmationId);
  if (semantic)
    expect(
      confirmed.result.results.models.every(
        (row: any) => row.cost_evidence === "scripted_fixture_usage",
      ),
    ).toBe(true);
  expect(confirmed.result.results.lineage.fresh_test_claim).toBe(true);
  expect(
    confirmed.result.results.criteria_generation_cost.amortized_per_task_usd,
  ).toBeNull();
  expect(
    confirmed.result.results.criteria_generation_cost.shared_one_time_setup,
  ).toBe(true);
  await region
    .getByText("Propose checks from training examples", { exact: true })
    .click();
  const preparation = region.getByRole("form", {
    name: "Prepare criteria generation",
    exact: true,
  });
  if (semantic) {
    await preparation
      .getByRole("combobox", { name: "Generated check type", exact: true })
      .selectOption("episode_semantic_rubrics");
    await preparation
      .getByLabel("Dimension 1 owner requirement", { exact: true })
      .fill("Owned browser reference requirement");
  }
  await preparation
    .getByRole("combobox", { name: "Training dataset", exact: true })
    .selectOption(process.env.CUTBACK_CRITERIA_DATASET!);
  await preparation
    .getByLabel("Task and desired behavior")
    .fill("Owned browser preparation without inference");
  const identities: string[] = [];
  let createdId = "";
  await page.route("**" + base + "/criteria/drafts", async (route) => {
    identities.push(route.request().postDataJSON().request_id);
    const response = await route.fetch();
    createdId = (await response.json()).id;
    if (identities.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await preparation
    .getByRole("button", { name: "Freeze generation plan", exact: true })
    .click();
  await expect(preparation.getByRole("alert")).toBeVisible();
  await preparation
    .getByRole("button", { name: "Freeze generation plan", exact: true })
    .click();
  await expect(preparation.getByRole("status")).toContainText(
    "Generation plan frozen",
  );
  await expect(selected).toContainText("PENDING");
  expect(identities.length).toBe(2);
  expect(identities[0]).toBe(identities[1]);
  await expect(selected).toContainText(createdId);
  await selected
    .getByLabel("Cancellation reason")
    .fill("owned-unapproved-draft-cancellation");
  await selected
    .getByRole("button", { name: "Cancel without replay", exact: true })
    .click();
  await expect(selected).toContainText("CANCELLED");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).toEqual({ local: 0, session: 0 });
  const viewer = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const other = await viewer.newPage();
  await other.goto("/dashboard/?project=" + process.env.CUTBACK_UI_PROJECT);
  await other
    .getByLabel("Control API key")
    .fill(process.env.CUTBACK_CRITERIA_VIEWER!);
  await other
    .getByRole("button", { name: "Connect to this collector", exact: true })
    .click();
  await other.getByRole("button", { name: "Experiments", exact: true }).click();
  const view = other.getByRole("region", {
    name: "Reviewed evaluation criteria",
    exact: true,
  });
  await view
    .getByRole("button", {
      name: "Inspect criteria " + process.env.CUTBACK_CRITERIA_ID,
      exact: true,
    })
    .click();
  await expect(
    view.getByRole("button", { name: "Read authorized content", exact: true }),
  ).toHaveCount(0);
  await expect(
    view.getByRole("button", { name: "Freeze generation plan", exact: true }),
  ).toHaveCount(0);
  expect((await new AxeBuilder({ page: other }).analyze()).violations).toEqual(
    [],
  );
  await viewer.close();
  expect(errors).toEqual([]);
});
