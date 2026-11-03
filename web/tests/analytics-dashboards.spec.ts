import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";

test("analytical dashboards preserve uncertain saves, current access, unknowns and exact revisions", async ({
  page,
  context,
}) => {
  test.skip(
    !process.env.CUTBACK_UI_ANALYTICS,
    "Requires the dedicated owned analytical fixture.",
  );
  const project = process.env.CUTBACK_UI_PROJECT!,
    token = process.env.CUTBACK_UI_KEY!;
  const base = `/v1/projects/${project}`,
    headers = { Authorization: "Bearer " + token };
  const privateValues = [token];
  try {
    const viewerResult = await page.request.post(base + "/keys", {
      headers,
      data: { role: "viewer" },
    });
    expect(viewerResult.status()).toBe(201);
    const viewerKey = await viewerResult.json();
    privateValues.push(viewerKey.token);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/dashboard/?project=" + project);
    await page.getByLabel("Control API key").fill(token);
    await page
      .getByRole("button", { name: "Connect to this collector" })
      .click();
    await page.getByRole("button", { name: "Costs", exact: true }).click();
    await page
      .getByRole("button", { name: "Saved charts", exact: true })
      .click();
    const workspace = page.getByRole("region", {
      name: "Shared analytical dashboards",
      exact: true,
    });
    await workspace
      .getByRole("button", { name: "Create analytical dashboard", exact: true })
      .click();
    const form = workspace.getByRole("form", {
      name: "Create analytical dashboard",
      exact: true,
    });
    await form
      .getByLabel("Analytical dashboard name", { exact: true })
      .fill("owned-team-health");
    const first = form.getByRole("group", { name: "Chart 1", exact: true });
    await expect(
      first.getByRole("combobox", { name: "Metric", exact: true }),
    ).toHaveValue("observed_attempts");
    await expect(
      first.getByRole("combobox", { name: "Group by", exact: true }),
    ).toBeDisabled();
    // The collector commits, but the first browser save reply is deliberately lost.
    const bodies: unknown[] = [];
    let drop = true;
    await page.route("**/analytics-dashboards", async (route) => {
      if (route.request().method() === "POST") {
        bodies.push(route.request().postDataJSON());
        if (drop) {
          drop = false;
          const result = await route.fetch();
          expect(result.status()).toBe(201);
          await route.abort("failed");
          return;
        }
      }
      await route.continue();
    });
    await form
      .getByRole("button", { name: "Save analytical dashboard", exact: true })
      .click();
    await expect(form.getByText(/Save outcome is unconfirmed/)).toBeVisible();
    await expect(form.getByLabel("Analytical dashboard name")).toBeDisabled();
    await form
      .getByRole("button", { name: "Retry same analytical save", exact: true })
      .click();
    await expect(
      workspace.getByRole("heading", {
        name: "owned-team-health",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      workspace.getByRole("article", {
        name: "Analytical panel traffic",
        exact: true,
      }),
    ).toBeVisible();
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    await page.unroute("**/analytics-dashboards");
    const listing = await page.request.get(base + "/analytics-dashboards", {
      headers,
    });
    const matches = (await listing.json()).items.filter(
      (item: { key: string }) => item.key === "owned-team-health",
    );
    expect(matches).toHaveLength(1);
    const chart = matches[0];
    expect(chart.revision).toBe(1);
    const cost = workspace.getByRole("article", {
      name: "Analytical panel cost",
      exact: true,
    });
    await expect(cost.getByText(/1 unpriced/)).toBeVisible();
    await expect(
      cost
        .getByText("owned-<img src=x onerror=alert(1)>", { exact: true })
        .first(),
    ).toBeVisible();
    expect(await workspace.locator("img").count()).toBe(0);
    await cost.getByText("Exact values and coverage", { exact: true }).click();
    await expect(
      cost.getByRole("cell", { name: "Unknown", exact: true }).first(),
    ).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await workspace
      .getByRole("button", { name: "Download analytical data", exact: true })
      .click();
    expect((await downloadPromise).suggestedFilename()).toBe(
      "cutback-analytics.json",
    );

    // A different editor wins before this browser's stale edit.
    const saved = await page.request.get(
      base + "/analytics-dashboards/" + chart.id,
      { headers },
    );
    const definition = await saved.json();
    const edited = await page.request.post(base + "/analytics-dashboards", {
      headers,
      data: {
        dashboard_id: chart.id,
        key: definition.key,
        expected_revision: 1,
        environment: definition.environment,
        window_days: 30,
        panels: definition.panels,
      },
    });
    expect(edited.status()).toBe(201);
    await workspace
      .getByRole("button", { name: "Edit analytical dashboard", exact: true })
      .click();
    const edit = workspace.getByRole("form", {
      name: "Edit analytical dashboard",
      exact: true,
    });
    await edit
      .getByRole("combobox", { name: "Analytical period", exact: true })
      .selectOption("90");
    await edit
      .getByRole("button", { name: "Save analytical dashboard", exact: true })
      .click();
    await expect(
      workspace.getByRole("alert").filter({ hasText: /dashboard changed/ }),
    ).toBeVisible();
    await workspace
      .getByRole("button", { name: "Refresh analytical data", exact: true })
      .click();
    await expect(workspace.getByText(/revision 2/)).toBeVisible();
    await expect(edit).toHaveCount(0);
    await expect(
      workspace.getByRole("button", {
        name: "Edit analytical dashboard",
        exact: true,
      }),
    ).toBeEnabled();

    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    if (process.env.CUTBACK_UI_VISUAL_DIR)
      await workspace.screenshot({
        path: path.join(
          process.env.CUTBACK_UI_VISUAL_DIR,
          "analytics-mobile.png",
        ),
      });
    await page.setViewportSize({ width: 1365, height: 900 });
    if (process.env.CUTBACK_UI_VISUAL_DIR)
      await workspace.screenshot({
        path: path.join(
          process.env.CUTBACK_UI_VISUAL_DIR,
          "analytics-desktop.png",
        ),
      });

    const viewer = await context.newPage();
    await viewer.goto("/dashboard/?project=" + project);
    await viewer.getByLabel("Control API key").fill(viewerKey.token);
    await viewer
      .getByRole("button", { name: "Connect to this collector" })
      .click();
    await viewer.getByRole("button", { name: "Costs", exact: true }).click();
    await viewer
      .getByRole("button", { name: "Saved charts", exact: true })
      .click();
    const shared = viewer.getByRole("region", {
      name: "Shared analytical dashboards",
      exact: true,
    });
    await shared
      .getByRole("button", { name: "owned-team-health · staging", exact: true })
      .click();
    await expect(
      shared.getByRole("article", {
        name: "Analytical panel traffic",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      shared.getByRole("button", {
        name: "Edit analytical dashboard",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      shared.getByRole("button", {
        name: "Create analytical dashboard",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(
      (
        await page.request.delete(base + "/keys/" + viewerKey.key_id, {
          headers,
        })
      ).status(),
    ).toBe(200);
    await shared
      .getByRole("button", { name: "Refresh analytical data", exact: true })
      .click();
    await expect(shared.getByRole("article")).toHaveCount(0);
    for (const surface of [page, viewer]) {
      const stored = await surface.evaluate(() =>
        JSON.stringify({
          local: { ...localStorage },
          session: { ...sessionStorage },
          cookies: document.cookie,
        }),
      );
      for (const value of privateValues) expect(stored).not.toContain(value);
    }
    await workspace
      .getByRole("button", { name: "Delete analytical dashboard", exact: true })
      .click();
    await expect(
      workspace.getByRole("button", {
        name: "owned-team-health · staging",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(
      (
        await page.request.get(base + "/analytics-dashboards/" + chart.id, {
          headers,
        })
      ).status(),
    ).toBe(404);
    await viewer.close();
    expect(errors).toEqual([]);
  } catch (cause) {
    for (const surface of context.pages()) {
      await surface
        .locator('input[type="password"]')
        .fill("")
        .catch(() => {});
    }
    let safe = String(cause);
    for (const value of privateValues)
      safe = safe.replaceAll(value, "<credential-omitted>");
    throw new Error(safe);
  }
});
