import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.skip(
  !process.env.CUTBACK_UI_INCIDENT,
  "Requires the dedicated owned incident fixture supplied by the comments acceptance test",
);

test("encrypted team comments, independent revisions, current viewer access and erasure", async ({
  page,
}) => {
  const project = process.env.CUTBACK_UI_PROJECT!;
  const token = process.env.CUTBACK_UI_KEY!;
  const incident = process.env.CUTBACK_UI_INCIDENT!;
  const base = `/v1/projects/${project}`;
  const headers = { Authorization: "Bearer " + token };
  const body =
    "OWNED_COMMENT_BODY <script>window.ownedCommentAttack=1</script>";
  const revised = "OWNED_COMMENT_REVISION_TWO";
  const privateValues = [token, body, revised];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let viewerKey: { token: string; key_id: string } | null = null;
  try {
    async function connect(surface: typeof page, credential: string) {
      await surface.goto("/dashboard/?project=" + project);
      await surface.getByLabel("Control API key").fill(credential);
      await surface
        .getByRole("button", { name: "Connect to this collector" })
        .click();
      await surface
        .getByRole("button", { name: "Monitoring", exact: true })
        .click();
      await surface
        .getByRole("button", {
          name: "Open incident " + incident.slice(0, 8),
          exact: true,
        })
        .click();
      return surface.getByRole("region", {
        name: "Incident comments",
        exact: true,
      });
    }
    const discussion = await connect(page, token);
    await discussion
      .getByText("Authorize encrypted team comments", { exact: true })
      .click();
    const authorization = discussion.getByRole("form", {
      name: "Comment storage authorization",
      exact: true,
    });
    await authorization
      .getByLabel("Authorization reference", { exact: true })
      .fill("owned-browser-comments");
    await authorization
      .getByLabel("Comment data rights")
      .selectOption("synthetic");
    await authorization
      .getByLabel(
        "I authorize encrypted comment text to be shared with current project readers",
      )
      .check();
    await authorization
      .getByRole("button", { name: "Authorize comment storage", exact: true })
      .click();
    await expect(
      discussion.getByText(
        "Encrypted comment storage authorized for this incident's environment.",
        { exact: true },
      ),
    ).toBeVisible();
    const create = discussion.getByRole("form", {
      name: "New incident comment",
      exact: true,
    });
    await create
      .getByLabel("Approved comment policy")
      .selectOption({ label: "owned-browser-comments · up to 7 days" });
    await create.getByLabel("Comment text", { exact: true }).fill(body);
    await create
      .getByRole("button", { name: "Post encrypted comment", exact: true })
      .click();
    const comment = discussion.getByRole("article", {
      name: "Incident comment 1",
      exact: true,
    });
    await expect(comment).toBeVisible();
    await expect(comment.getByText(body, { exact: true })).toHaveCount(0);
    const read = comment.getByRole("form", {
      name: "Read comment 1",
      exact: true,
    });
    await read.getByLabel("Comment read purpose").fill("owned-incident-review");
    await read
      .getByRole("button", { name: "Read comment text", exact: true })
      .click();
    await expect(comment.getByText(body, { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => (window as any).ownedCommentAttack),
    ).toBeUndefined();
    await comment
      .getByRole("button", { name: "Edit this comment", exact: true })
      .click();
    const edit = comment.getByRole("form", {
      name: "Revise incident comment",
      exact: true,
    });
    await edit.getByLabel("Revised comment text").fill(revised);
    await edit
      .getByRole("button", { name: "Save encrypted revision", exact: true })
      .click();
    await expect(comment.getByText(body, { exact: true })).toHaveCount(0);
    await read
      .getByRole("button", { name: "Read comment text", exact: true })
      .click();
    await expect(comment.getByText(revised, { exact: true })).toBeVisible();
    await comment
      .getByRole("button", {
        name: "View retained comment revisions",
        exact: true,
      })
      .click();
    await expect(comment.getByText(/2 retained revisions/)).toBeVisible();
    await read.getByLabel("Comment revision").selectOption("1");
    await read
      .getByRole("button", { name: "Read comment text", exact: true })
      .click();
    await expect(comment.getByText(body, { exact: true })).toBeVisible();

    const viewerResponse = await page.request.post(base + "/keys", {
      headers,
      data: { role: "viewer" },
    });
    expect(viewerResponse.status()).toBe(201);
    viewerKey = await viewerResponse.json();
    privateValues.push(viewerKey!.token);
    const viewer = await page.context().newPage();
    viewer.on("pageerror", (error) => errors.push(error.message));
    const shared = await connect(viewer, viewerKey!.token);
    await expect(
      shared.getByRole("form", { name: "New incident comment", exact: true }),
    ).toHaveCount(0);
    await expect(shared.getByText(revised, { exact: true })).toHaveCount(0);
    const viewerRead = shared.getByRole("form", {
      name: "Read comment 1",
      exact: true,
    });
    await viewerRead
      .getByLabel("Comment read purpose")
      .fill("owned-team-triage");
    await viewerRead
      .getByRole("button", { name: "Read comment text", exact: true })
      .click();
    await expect(shared.getByText(revised, { exact: true })).toBeVisible();
    for (const surface of [page, viewer]) {
      await surface.setViewportSize({ width: 390, height: 844 });
      expect(
        await surface.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      const accessibility = await new AxeBuilder({ page: surface }).analyze();
      expect(accessibility.violations).toEqual([]);
      const retained = await surface.evaluate(() =>
        JSON.stringify({
          local: { ...localStorage },
          session: { ...sessionStorage },
          cookies: document.cookie,
        }),
      );
      for (const value of privateValues) expect(retained).not.toContain(value);
    }
    expect(
      (
        await page.request.delete(base + "/keys/" + viewerKey!.key_id, {
          headers,
        })
      ).status(),
    ).toBe(200);
    await shared
      .getByRole("button", {
        name: "Refresh comments and hide text",
        exact: true,
      })
      .click();
    await expect(shared.getByText(revised, { exact: true })).toHaveCount(0);
    await expect(shared.getByRole("alert")).toBeVisible();
    await viewer.close();

    await page.setViewportSize({ width: 1365, height: 900 });
    const erase = comment.getByRole("form", {
      name: "Delete comment 1",
      exact: true,
    });
    await erase
      .getByLabel("Erase all retained text revisions of this comment")
      .check();
    await erase
      .getByRole("button", { name: "Delete comment text", exact: true })
      .click();
    await expect(
      comment.getByText(
        "Comment deleted. Retained metadata records the erasure.",
        { exact: true },
      ),
    ).toBeVisible();
    await expect(comment.getByText(body, { exact: true })).toHaveCount(0);
    await expect(comment.getByText(revised, { exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
  } catch (error) {
    let safe = String(error);
    for (const value of privateValues)
      safe = safe.replaceAll(value, "<omitted>");
    throw new Error(safe);
  } finally {
    if (viewerKey)
      await page.request.delete(base + "/keys/" + viewerKey.key_id, {
        headers,
      });
  }
});
