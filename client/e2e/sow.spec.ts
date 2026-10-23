import { expect, test, type Page, type Request, type Route } from "@playwright/test";

import sample from "./fixtures/sow-ms01.json" with { type: "json" };

// SOW screen (E6.5). The SOW API is faked at the proxy by one stateful handler; the editor (TipTap),
// panels and dialogs are real. The document is the server's own TipTap for the MS01.1 fixture.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const API = "**/api/proxy/api/v1";
const SOW_ID = "0190f3a2-7b1c-7d4e-8f00-00000000a0a1";
const KV = "0190f3a2-7b1c-7d4e-8f00-00000000c0a1";
const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body), headers });
const problem = (route: Route, status: number, code: string, detail = "") =>
  route.fulfill({
    status,
    contentType: "application/problem+json",
    body: JSON.stringify({ title: code, status, code, detail, errors: [], correlation_id: "c" }),
  });

const project = (pipeline_state = "SOW_REVIEW") => ({
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing",
  client_name: null,
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state,
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
});

type Version = {
  id: string;
  version: number;
  status: string;
  source: string;
  sha256: string | null;
  parent_version: number | null;
  knowledge_version_id: string;
  error_reason: string | null;
  author: string | null;
  created_at: string;
};

const summary = (n: number, status = "IN_REVIEW", extra: Partial<Version> = {}): Version => ({
  id: `0190f3a2-7b1c-7d4e-8f00-0000000001${String(n).padStart(2, "0")}`,
  version: n,
  status,
  source: n === 1 ? "GENERATED" : "EDITED",
  sha256: status === "GENERATION_FAILED" ? null : `${n}`.repeat(64).slice(0, 64),
  parent_version: n > 1 ? n - 1 : null,
  knowledge_version_id: KV,
  error_reason: null,
  author: n > 1 ? "fde" : null,
  created_at: `2026-10-2${n % 10}T10:00:00Z`,
  ...extra,
});

type Fake = {
  versions: Version[];
  etag: number;
  pipeline: string;
  save: "ok" | "conflict" | "locked" | "expired";
  saves: { body: Record<string, unknown>; ifMatch: string | null }[];
  regenerates: Record<string, unknown>[];
  diff?: unknown;
  afterRegenerate?: () => void;
  sourceDeleted?: boolean;
};

const fake = (versions: Version[], more: Partial<Fake> = {}): Fake => ({
  versions,
  etag: 1,
  pipeline: "SOW_REVIEW",
  save: "ok",
  saves: [],
  regenerates: [],
  ...more,
});

const current = (f: Fake) => [...f.versions].sort((a, b) => b.version - a.version)[0]!;

async function serve(page: Page, f: Fake) {
  await page.route(`${API}/projects`, (r) => json(r, { items: [project(f.pipeline)] }));
  await page.route(`${API}/projects/${PROJECT}/sow**`, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.split(`/projects/${PROJECT}/sow`)[1] ?? "";
    if (path === "" && req.method() === "GET") {
      const c = current(f);
      return json(route, {
        sow_id: SOW_ID,
        current: c,
        approved: f.versions.find((v) => v.status === "APPROVED") ?? null,
        etag: `"${f.etag}"`,
      });
    }
    if (path === "/versions" && req.method() === "GET")
      return json(route, { items: [...f.versions].sort((a, b) => b.version - a.version) });
    if (path === "/versions" && req.method() === "POST") {
      f.saves.push({ body: req.postDataJSON(), ifMatch: req.headers()["if-match"] ?? null });
      if (f.save === "locked") return problem(route, 423, "SOW_LOCKED", "approved");
      if (f.save === "expired") return problem(route, 401, "UNAUTHENTICATED", "session expired");
      if (f.save === "conflict") {
        f.versions.push(summary(current(f).version + 1));
        f.etag += 1;
        return problem(route, 409, "SOW_VERSION_CONFLICT", "newer version");
      }
      const next = summary(current(f).version + 1);
      f.versions.push(next);
      f.etag += 1;
      return json(route, next, 201, { etag: `"${f.etag}"` });
    }
    if (path === "/regenerate" && req.method() === "POST") {
      f.regenerates.push(req.postDataJSON() ?? {});
      f.afterRegenerate?.();
      return json(route, { run: { id: "run-1", type: "SowRegenerate", state: "RUNNING" } }, 202);
    }
    const diff = /^\/versions\/(\d+)\/diff\/(\d+)$/.exec(path);
    if (diff) return json(route, f.diff ?? { version: 1, other: 2, sections: [] });
    const cite = /^\/versions\/(\d+)\/citations\/([^/]+)$/.exec(path);
    if (cite)
      return json(route, {
        okf_ref_id: decodeURIComponent(cite[2]!),
        kind: "chunk",
        document_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000d0c1",
        title: f.sourceDeleted ? null : "RFQ cables.pdf",
        location: { page: 3, line: 12 },
        passage: "The Planner approves every quote above 5000 EUR.",
        truncated: false,
        source_available: !f.sourceDeleted,
      });
    const one = /^\/versions\/(\d+)$/.exec(path);
    if (one) {
      const v = f.versions.find((x) => x.version === Number(one[1]));
      if (!v) return problem(route, 404, "NOT_FOUND");
      const failed = v.status === "GENERATION_FAILED";
      return json(route, {
        ...v,
        content_tiptap: failed ? null : sample.content_tiptap,
        structured: failed ? null : sample.structured,
        citations: [],
      });
    }
    return problem(route, 404, "NOT_FOUND");
  });
}

async function open(page: Page, baseURL: string, f: Fake) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await serve(page, f);
  await page.goto(`/projects/${PROJECT}/sow`);
  await expect(page.getByTestId("sow-screen")).toBeVisible({ timeout: 30_000 }); // first compile
}

const editor = (page: Page) => page.getByTestId("sow-editor");
const mutations = (requests: Request[]) =>
  requests.filter((r) => r.url().includes("/api/proxy/") && r.method() !== "GET");

test("e6_5_t01 save creates a new version shown in the list", async ({ page, baseURL }) => {
  const f = fake([summary(1)]);
  await open(page, baseURL!, f);
  await expect(page.getByRole("heading", { name: "SOW v1" })).toBeVisible();
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" for every investor");
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved as v2")).toBeVisible();
  await expect(page.getByRole("heading", { name: "SOW v2" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Versions (2)" })).toBeVisible();
  const [sent] = f.saves;
  expect(sent?.ifMatch).toBe('"1"');
  expect(sent?.body.base_version).toBe(1);
  expect(JSON.stringify(sent?.body.content_tiptap)).toContain("for every investor");
});

test("e6_5_t02 an approved version is read-only; a 423 on save locks the editor", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, fake([summary(1, "APPROVED")]));
  await expect(page.getByTestId("sow-locked")).toContainText("Approved");
  await expect(editor(page)).toHaveAttribute("contenteditable", "false");
  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Create new version" })).toBeVisible();

  await page.unrouteAll({ behavior: "ignoreErrors" });
  const f = fake([summary(1)], { save: "locked" });
  await open(page, baseURL!, f);
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.type("x");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("sow-locked")).toBeVisible();
  await expect(editor(page)).toHaveAttribute("contenteditable", "false");
  await expect(page.getByRole("button", { name: "Create new version" })).toBeVisible();
});

test("e6_5_t03 a concurrent save shows the conflict with its diff and loses nothing", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1)], {
    save: "conflict",
    diff: {
      version: 1,
      other: 2,
      sections: [
        {
          section_id: "scope",
          change: "MODIFIED",
          items: [{ id: "in_scope", change: "MODIFIED" }],
        },
      ],
    },
  });
  await open(page, baseURL!, f);
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" mine");
  await page.getByRole("button", { name: "Save" }).click();
  const dialog = page.getByRole("dialog", { name: "Someone saved a newer version" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-change="MODIFIED"]')).toBeVisible();
  await expect(editor(page)).toContainText("mine"); // the draft is still there
  await expect(dialog.getByRole("button", { name: "Copy my edits" })).toBeVisible();
  await dialog.getByRole("button", { name: "Reload v2" }).click();
  await expect(page.getByRole("heading", { name: "SOW v2" })).toBeVisible();
  expect(f.versions).toHaveLength(2); // nothing was overwritten
});

test("e6_5_t04 a template heading cannot be deleted; its content stays editable", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, fake([summary(1)]));
  const heading = editor(page).locator('section[data-section="scope"] > h2');
  await heading.click({ clickCount: 3 });
  await page.keyboard.press("Delete");
  await page.keyboard.press("Backspace");
  await expect(heading).toHaveText("Scope");
  await expect(page.getByText("Unsaved changes")).toHaveCount(0);
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" edited");
  await expect(editor(page)).toContainText("OTP sign-in with device limits edited");
});

test("e6_5_t05 a citation opens its source in the side panel", async ({ page, baseURL }) => {
  await open(page, baseURL!, fake([summary(1)]));
  await editor(page).locator(".sow-cite").nth(2).click();
  const panel = page.getByTestId("citation-detail");
  await expect(panel).toContainText("[3]");
  await expect(panel).toContainText("RFQ cables.pdf");
  await expect(panel).toContainText("page 3, line 12");
  await expect(page.getByTestId("citation-passage")).toContainText("approves every quote");
  const source = panel.getByRole("link", { name: "Open source" });
  await expect(source).toHaveAttribute("href", /\/sow\/versions\/1\/citations\/.+\/source$/);
});

test("e6_5_t06 a failed generation offers Regenerate with a hint, then shows progress", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1, "GENERATION_FAILED", { error_reason: "SCHEMA_INVALID" })]);
  f.afterRegenerate = () => {
    f.pipeline = "SOW_GENERATING";
    setTimeout(() => {
      f.versions.push(summary(2, "GENERATED"));
      f.pipeline = "SOW_REVIEW";
    }, 1500);
  };
  await open(page, baseURL!, f);
  await expect(page.getByTestId("sow-failed")).toContainText("SCHEMA_INVALID");
  await page.getByTestId("sow-failed").getByRole("button", { name: "Regenerate" }).click();
  await page.getByLabel("Hint").fill("Add a QA module");
  await page.getByRole("dialog").getByRole("button", { name: "Regenerate" }).click();
  await expect(page.getByTestId("sow-generating").first()).toBeVisible();
  expect(f.regenerates).toEqual([{ hint: "Add a QA module" }]);
  await expect(page.getByText("v2 is ready.")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "SOW v2" })).toBeVisible();
});

test("e6_5_t07 pasted HTML with script and img is stripped before saving", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1)]);
  await open(page, baseURL!, f);
  const errors: string[] = [];
  page.on("dialog", (d) => {
    errors.push(d.message());
    void d.dismiss();
  });
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.press("End");
  await editor(page).evaluate((el) => {
    const data = new DataTransfer();
    data.setData(
      "text/html",
      '<img src=x onerror="alert(1)"><script>alert(2)</script><iframe src="http://evil"></iframe> pasted',
    );
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true }));
  });
  await expect(editor(page)).toContainText("pasted");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved as v2")).toBeVisible();
  const saved = JSON.stringify(f.saves[0]?.body.content_tiptap);
  for (const bad of ["script", "iframe", "onerror", "alert(", "evil", '"image"']) {
    expect(saved).not.toContain(bad);
  }
  expect(errors).toEqual([]); // nothing executed
});

test("e6_5_t08 compare shows section badges from the API diff", async ({ page, baseURL }) => {
  const f = fake([summary(1, "SUPERSEDED"), summary(2), summary(3)], {
    diff: {
      version: 1,
      other: 3,
      sections: [
        { section_id: "overview", change: "UNCHANGED", items: [] },
        {
          section_id: "features",
          change: "MODIFIED",
          items: [{ id: "F-ACCESS-1", change: "MODIFIED" }],
        },
      ],
    },
  });
  const requests: Request[] = [];
  page.on("request", (r) => requests.push(r));
  await open(page, baseURL!, f);
  await page.getByRole("button", { name: "Compare" }).click();
  const drawer = page.getByTestId("sow-diff");
  await expect(drawer.locator('[data-section="features"] [data-change="MODIFIED"]')).toBeVisible();
  await expect(drawer.locator('[data-item="F-ACCESS-1"]')).toContainText("MODIFIED");
  await expect(drawer.locator('[data-section="overview"] [data-change="UNCHANGED"]')).toBeVisible();
  expect(requests.some((r) => r.url().endsWith("/sow/versions/1/diff/3"))).toBe(true);
  expect(mutations(requests)).toEqual([]);
});

test("e6_5 leaving by an in-app link with unsaved edits asks first", async ({ page, baseURL }) => {
  await open(page, baseURL!, fake([summary(1)]));
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.type("x");
  const knowledge = page.getByRole("link", { name: "Knowledge" }).first();
  await knowledge.click();
  const confirm = page.getByRole("dialog", { name: "Discard unsaved edits?" });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT}/sow$`));
  await expect(page.getByText("Unsaved changes")).toBeVisible();
  await knowledge.click();
  await page
    .getByRole("dialog", { name: "Discard unsaved edits?" })
    .getByRole("button", { name: "Discard" })
    .click();
  await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT}/knowledge$`));
});

test("e6_5 an expired session keeps the draft: sign in again, restore, save", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1)], { save: "expired" });
  await open(page, baseURL!, f);
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" kept across sign-in");
  page.once("dialog", (d) => void d.accept()); // the browser's leave-page prompt, if any
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page).toHaveURL(/\/login-fde/);
  f.save = "ok";
  await page.goto(`/projects/${PROJECT}/sow`); // signed in again
  const banner = page.getByTestId("sow-restore");
  await expect(banner).toContainText("unsaved edits to v1");
  await banner.getByRole("button", { name: "Restore my edits" }).click();
  await expect(editor(page)).toContainText("kept across sign-in");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved as v2")).toBeVisible();
  expect(JSON.stringify(f.saves.at(-1)?.body.content_tiptap)).toContain("kept across sign-in");
});

test("e6_5 a list of 150 versions is virtualized", async ({ page, baseURL }) => {
  const many = Array.from({ length: 150 }, (_, i) =>
    summary(i + 1, i === 149 ? "IN_REVIEW" : "SUPERSEDED"),
  );
  await open(page, baseURL!, fake(many));
  await page.getByRole("tab", { name: "Versions (150)" }).click();
  const rows = page.getByTestId("sow-versions").locator(".ant-table-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeLessThan(60); // only what fits on screen is rendered
  await rows.nth(1).click();
  await expect(page.getByRole("heading", { name: "SOW v149" })).toBeVisible();
});

test("e6_5 a citation whose document was deleted says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, fake([summary(1)], { sourceDeleted: true }));
  await editor(page).locator(".sow-cite").first().click();
  const panel = page.getByTestId("citation-detail");
  await expect(panel).toContainText("Source deleted");
  await expect(page.getByTestId("citation-passage")).toContainText("approves every quote");
  await expect(panel.getByRole("link", { name: "Open source" })).toHaveCount(0);
});
