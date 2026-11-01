import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("team dashboard creation, pinned revisions, explicit update, viewer access and deletion", async ({
  page,
}) => {
  const project = process.env.CUTBACK_UI_PROJECT!;
  const token = process.env.CUTBACK_UI_KEY!;
  const base = `/v1/projects/${project}`;
  const privateValues = [token];
  try {
    const headers = { Authorization: "Bearer " + token };
    const workflow =
      "shared-dashboard-" + crypto.randomUUID().replaceAll("-", "");
    for (const [key, pageName] of [
      ["owned-traces", "traces"],
      ["owned-open", "incidents"],
    ]) {
      const result = await page.request.post(base + "/saved-views", {
        headers,
        data: {
          view_id: crypto.randomUUID(),
          key,
          expected_revision: 0,
          environment: "staging",
          page: pageName,
          filter: { workflow },
          incident_state: pageName === "incidents" ? "open" : null,
        },
      });
      expect(result.status()).toBe(201);
    }
    const writerResponse = await page.request.post(base + "/keys", {
      headers,
      data: { role: "ingest", environment: "staging" },
    });
    expect(writerResponse.status()).toBe(201);
    const writer = await writerResponse.json();
    privateValues.push(writer.token);
    // Whole-second synthetic starts and power-of-two durations are exactly representable.
    const started = Math.floor(Date.now() / 1000) * 1_000_000_000;
    const observed = await page.request.post("/v1/ingest/spans", {
      headers: { Authorization: "Bearer " + writer.token },
      data: {
        spans: [0, 1].map(() => ({
          trace_id: crypto.randomUUID().replaceAll("-", ""),
          span_id: crypto.randomUUID().replaceAll("-", "").slice(0, 16),
          operation: "owned-reply",
          service: "synthetic-desk",
          workflow,
          kind: "chain",
          status: "error",
          started_ns: started,
          ended_ns: started + 1_048_576,
        })),
      },
    });
    expect(observed.status()).toBe(200);
    expect(
      (
        await page.request.delete(base + "/keys/" + writer.key_id, { headers })
      ).status(),
    ).toBe(200);
    const viewerResponse = await page.request.post(base + "/keys", {
      headers,
      data: { role: "viewer" },
    });
    expect(viewerResponse.status()).toBe(201);
    const viewerKey = await viewerResponse.json();
    privateValues.push(viewerKey.token);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/dashboard/?project=" + project);
    await page.getByLabel("Control API key").fill(token);
    await page
      .getByRole("button", { name: "Connect to this collector" })
      .click();
    await page.getByRole("button", { name: "Monitoring", exact: true }).click();
    const team = page.getByRole("region", {
      name: "Shared team dashboards",
      exact: true,
    });
    await expect(
      team.getByRole("heading", { name: "Shared team dashboards" }),
    ).toBeVisible();
    await team.getByText("Compose a shared dashboard", { exact: true }).click();
    const form = team.getByRole("form", {
      name: "Create shared dashboard",
      exact: true,
    });
    await form
      .getByLabel("Dashboard key", { exact: true })
      .fill("owned-team-overview");
    await form
      .getByLabel("Include owned-traces (revision 1)", { exact: true })
      .check();
    await form
      .getByLabel("Include owned-open (revision 1)", { exact: true })
      .check();
    await form
      .getByLabel("I reviewed the selected saved-view revisions and filters")
      .check();
    await form
      .getByRole("button", { name: "Create shared dashboard", exact: true })
      .click();
    const selected = team.getByRole("region", {
      name: "Shared dashboard owned-team-overview",
      exact: true,
    });
    const panel = selected.getByRole("article", {
      name: "Dashboard panel owned-traces",
      exact: true,
    });
    await expect(
      panel.getByRole("button", { name: /^Inspect trace / }),
    ).toHaveCount(2);
    await expect(panel.getByText(/execution error/).first()).toBeVisible();
    await expect(
      selected.getByText(/revision 1/, { exact: false }).first(),
    ).toBeVisible();
    const listing = await page.request.get(base + "/dashboards", {
      headers: { Authorization: "Bearer " + token },
    });
    expect(listing.status()).toBe(200);
    const dashboard = (await listing.json()).items.find(
      (row: { key: string }) => row.key === "owned-team-overview",
    );
    expect(dashboard).toBeTruthy();

    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 1365, height: 900 });
    await panel
      .getByRole("button", { name: /^Inspect trace / })
      .first()
      .click();
    await expect(
      selected.getByRole("region", {
        name: "Trace investigation",
        exact: true,
      }),
    ).toBeVisible();

    const views = await page.request.get(base + "/saved-views", {
      headers: { Authorization: "Bearer " + token },
    });
    const original = (await views.json()).items.find(
      (row: { key: string }) => row.key === "owned-traces",
    );
    const changed = await page.request.post(base + "/saved-views", {
      headers: { Authorization: "Bearer " + token },
      data: {
        ...original.specification,
        expected_revision: 1,
        filter: { workflow: "different-workflow" },
      },
    });
    expect(changed.status()).toBe(201);
    await selected
      .getByRole("button", { name: "Refresh dashboard", exact: true })
      .click();
    await expect(panel.getByText(/source changed/)).toBeVisible();
    await expect(
      panel.getByRole("button", { name: /^Inspect trace / }),
    ).toHaveCount(2);
    await team
      .getByRole("button", { name: "Refresh saved views", exact: true })
      .click();
    await selected
      .getByRole("button", { name: "Edit dashboard composition", exact: true })
      .click();
    const edit = team.getByRole("form", {
      name: "Update shared dashboard",
      exact: true,
    });
    await expect(
      edit.getByLabel("Include owned-traces (revision 2)", { exact: true }),
    ).toBeChecked();
    await edit
      .getByLabel("I reviewed the selected saved-view revisions and filters")
      .check();
    await edit
      .getByRole("button", { name: "Save dashboard revision", exact: true })
      .click();
    await expect(
      selected.getByRole("heading", {
        name: /owned-team-overview · revision 2/,
      }),
    ).toBeVisible();
    await expect(panel.getByText(/No matching retained records/)).toBeVisible();

    const viewer = await page.context().newPage();
    await viewer.goto("/dashboard/?project=" + project);
    await viewer.getByLabel("Control API key").fill(viewerKey.token);
    await viewer
      .getByRole("button", { name: "Connect to this collector" })
      .click();
    await viewer
      .getByRole("button", { name: "Monitoring", exact: true })
      .click();
    const shared = viewer.getByRole("region", {
      name: "Shared team dashboards",
      exact: true,
    });
    await shared
      .getByRole("button", {
        name: "Open shared dashboard owned-team-overview",
        exact: true,
      })
      .click();
    await expect(
      shared.getByRole("heading", { name: /owned-team-overview · revision 2/ }),
    ).toBeVisible();
    await expect(
      shared.getByRole("button", { name: "Edit dashboard composition" }),
    ).toHaveCount(0);
    await expect(shared.getByRole("form")).toHaveCount(0);
    for (const surface of [page, viewer]) {
      const stored = await surface.evaluate(() =>
        JSON.stringify({
          local: { ...localStorage },
          session: { ...sessionStorage },
          cookies: document.cookie,
        }),
      );
      expect(stored).not.toContain(token);
      expect(stored).not.toContain(viewerKey.token);
    }
    await selected
      .getByRole("button", {
        name: "Delete shared dashboard owned-team-overview",
        exact: true,
      })
      .click();
    await expect(
      team.getByRole("button", {
        name: "Open shared dashboard owned-team-overview",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(
      (
        await page.request.get(base + "/dashboards/" + dashboard.id, {
          headers: { Authorization: "Bearer " + token },
        })
      ).status(),
    ).toBe(404);
    await viewer.close();
    expect(
      (
        await page.request.delete(base + "/keys/" + viewerKey.key_id, {
          headers,
        })
      ).status(),
    ).toBe(200);
    expect(errors).toEqual([]);
  } catch (cause) {
    let safe = String(cause);
    for (const value of privateValues)
      safe = safe.replaceAll(value, "<credential-omitted>");
    throw new Error(safe);
  }
});
