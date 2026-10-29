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
  // E7.3: how POST /versions/{v}/approve answers, and what it was sent
  approve: "ok" | "stale" | "down" | "not_waiting";
  approvals: { version: number; body: Record<string, unknown> }[];
  approveDelayMs?: number;
  // E7.3 scope change: POST /new-version {reason} and GET /versions/{v}/impact
  newVersions: Record<string, unknown>[];
  impact?: unknown;
  impactAsked: number[];
};

const fake = (versions: Version[], more: Partial<Fake> = {}): Fake => ({
  versions,
  etag: 1,
  pipeline: "SOW_REVIEW",
  save: "ok",
  saves: [],
  regenerates: [],
  approve: "ok",
  approvals: [],
  newVersions: [],
  impactAsked: [],
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
    const approve = /^\/versions\/(\d+)\/approve$/.exec(path);
    if (approve && req.method() === "POST") {
      const n = Number(approve[1]);
      f.approvals.push({ version: n, body: req.postDataJSON() });
      if (f.approveDelayMs) await new Promise((r) => setTimeout(r, f.approveDelayMs));
      if (f.approve === "down")
        return problem(route, 503, "ORCHESTRATOR_UNAVAILABLE", "Temporal is unavailable");
      if (f.approve === "not_waiting")
        return problem(route, 409, "PIPELINE_NOT_WAITING", "not waiting for an approval");
      if (f.approve === "stale") {
        f.versions.push(summary(current(f).version + 1)); // another session saved first
        return problem(route, 409, "SOW_STALE", "the SOW changed");
      }
      const v = f.versions.find((x) => x.version === n)!;
      v.status = "APPROVED";
      f.pipeline = "SOW_APPROVED";
      return json(route, {
        version: n,
        status: "APPROVED",
        is_locked: true,
        approved_at: "2026-10-23T09:30:00Z",
        approved_by: "fde",
        sha256: v.sha256,
      });
    }
    if (path === "/new-version" && req.method() === "POST") {
      f.newVersions.push(req.postDataJSON());
      const approved = f.versions.find((v) => v.status === "APPROVED");
      if (!approved) return problem(route, 409, "NO_APPROVED_SOW", "nothing approved");
      if (current(f) !== approved) return problem(route, 409, "DRAFT_EXISTS", "a draft is open");
      const draft = summary(approved.version + 1, "IN_REVIEW", {
        sha256: approved.sha256,
        parent_version: approved.version,
      });
      f.versions.push(draft);
      f.pipeline = "SOW_REVIEW";
      f.etag += 1;
      return json(route, draft, 201, { etag: `"${f.etag}"` });
    }
    const impact = /^\/versions\/(\d+)\/impact$/.exec(path);
    if (impact) {
      f.impactAsked.push(Number(impact[1]));
      return json(route, f.impact);
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
  await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT}/knowledge$`), {
    timeout: 30_000, // the dev server compiles the knowledge page on its first visit
  });
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

// ---- E7.3 approval: POST /versions/{v}/approve {sha256} (E7.1) -------------------------------------

const approveButton = (page: Page) => page.getByRole("button", { name: "Approve", exact: true });
const confirmButton = (page: Page) => page.getByRole("button", { name: "Confirm approval" });

test("e7_3_t01 approve: the dialog shows the hash, then the banner and a locked editor", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1)]);
  await open(page, baseURL!, f);
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "Approve SOW v1?" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("11111111")).toBeVisible(); // the short hash, with a copy button
  await confirmButton(page).click();
  await expect(page.getByText("SOW v1 approved.")).toBeVisible();
  await expect(page.getByTestId("sow-locked")).toContainText("Approved");
  await expect(page.getByTestId("sow-approval")).toContainText("v1 approved by fde");
  await expect(page.getByTestId("sow-screen").locator('[data-status="APPROVED"]')).toHaveText(
    "Approved",
  );
  await expect(editor(page)).toHaveAttribute("contenteditable", "false");
  await expect(approveButton(page)).toHaveCount(0);
  expect(f.approvals).toEqual([{ version: 1, body: { sha256: "1".repeat(64) } }]);
  expect(f.pipeline).toBe("SOW_APPROVED");
});

test("e7_3_t02 a stale approval asks to reload and approves nothing", async ({ page, baseURL }) => {
  const f = fake([summary(1)], { approve: "stale" });
  await open(page, baseURL!, f);
  await approveButton(page).click();
  await confirmButton(page).click();
  const stale = page.getByRole("dialog", { name: "A newer version exists" });
  await expect(stale).toContainText("v1 was not approved");
  expect(f.approvals).toHaveLength(1); // never retried with another hash
  await stale.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByRole("heading", { name: "SOW v2" })).toBeVisible();
  await expect(page.getByTestId("sow-locked")).toHaveCount(0);
  expect(f.approvals).toHaveLength(1);
});

test("e7_3_t04 cancel sends nothing", async ({ page, baseURL }) => {
  const f = fake([summary(1)]);
  const requests: Request[] = [];
  page.on("request", (r) => requests.push(r));
  await open(page, baseURL!, f);
  await approveButton(page).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(mutations(requests)).toEqual([]);
  await expect(approveButton(page)).toBeEnabled();
});

test("e7_3_t05 a double click on confirm sends one approval", async ({ page, baseURL }) => {
  const f = fake([summary(1)], { approveDelayMs: 600 });
  await open(page, baseURL!, f);
  await approveButton(page).click();
  await confirmButton(page).dblclick();
  await expect(page.getByText("SOW v1 approved.")).toBeVisible();
  expect(f.approvals).toHaveLength(1);
});

test("e7_3_t06 orchestrator down: a toast, the version stays in review, Approve comes back", async ({
  page,
  baseURL,
}) => {
  const f = fake([summary(1)], { approve: "down" });
  await open(page, baseURL!, f);
  await approveButton(page).click();
  await confirmButton(page).click();
  await expect(page.getByText("Orchestrator unavailable, try again.")).toBeVisible();
  await expect(page.getByTestId("sow-locked")).toHaveCount(0);
  await expect(approveButton(page)).toBeEnabled();
  expect(f.versions[0]!.status).toBe("IN_REVIEW");
});

test("e7_3_t07 an admin on the FDE route gets the forbidden screen and no Approve", async ({
  page,
  baseURL,
}) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-admin", url: baseURL! }]);
  await serve(page, fake([summary(1)]));
  await page.goto(`/projects/${PROJECT}/sow`);
  await expect(page.getByText(/not allowed|forbidden|403/i).first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(approveButton(page)).toHaveCount(0);
});

test("e7_3 unsaved edits: Approve asks to save first", async ({ page, baseURL }) => {
  const f = fake([summary(1)]);
  await open(page, baseURL!, f);
  await page.getByText("OTP sign-in with device limits").click();
  await page.keyboard.type(" x");
  await approveButton(page).click();
  await expect(
    page.getByText("Save your edits first: approval signs off a saved version."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save now" }).click();
  await expect(page.getByText("Saved as v2")).toBeVisible();
  expect(f.approvals).toEqual([]);
});

test("e7_3 only the latest version in review can be approved", async ({ page, baseURL }) => {
  const f = fake([summary(1), summary(2)]);
  await open(page, baseURL!, f);
  await expect(approveButton(page)).toBeEnabled(); // v2, the latest
  await page.getByRole("tab", { name: "Versions (2)" }).click();
  await page
    .getByTestId("sow-versions")
    .locator(".ant-table-row")
    .filter({ hasText: "v1" })
    .click();
  await expect(page.getByRole("heading", { name: "SOW v1" })).toBeVisible();
  await expect(approveButton(page)).toBeDisabled();
});

test("e7_3 a pipeline that isn't waiting says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, fake([summary(1)], { approve: "not_waiting" }));
  await approveButton(page).click();
  await confirmButton(page).click();
  await expect(
    page.getByText("The pipeline is not waiting for an SOW approval right now."),
  ).toBeVisible();
});

// ---- E7.3 scope change: POST /new-version {reason}, GET /versions/{v}/impact (E7.2) ------------------

const approvedV3 = () => [
  summary(1, "SUPERSEDED"),
  summary(2, "SUPERSEDED"),
  summary(3, "APPROVED"),
];
const draftV4 = () => [...approvedV3(), summary(4, "IN_REVIEW", { parent_version: 3 })];
const impactOf = (subSkills: { skill_id: string; reason: string }[], more = {}) => ({
  version: 4,
  base_version: 3,
  changed_sections: [
    {
      section_id: "features",
      change: "MODIFIED",
      items: [{ id: "F-OTP", change: "MODIFIED" }],
    },
    { section_id: "overview", change: "UNCHANGED", items: [] },
  ],
  affected_macro_skills: [{ macro_category: "MS01", reason: "cites F-OTP" }],
  affected_sub_skills: subSkills,
  added_modules: [],
  reused_sub_skills_count: 12,
  ...more,
});
const twoAffected = [
  { skill_id: "MS01.2", reason: "cites F-OTP (MODIFIED)" },
  { skill_id: "MS01.4", reason: "cites F-OTP (MODIFIED)" },
];

test("e7_3_t03 the impact preview lists the API's affected Sub-Skills", async ({
  page,
  baseURL,
}) => {
  const f = fake(draftV4(), { impact: impactOf(twoAffected) });
  await open(page, baseURL!, f);
  await page.getByRole("tab", { name: "Impact" }).click();
  const panel = page.getByTestId("sow-impact");
  await expect(panel).toContainText("v4 against approved v3");
  await expect(panel).toContainText("2 Sub-Skills to rebuild, 1 Macro Skill affected, 12 reused");
  const ids = panel
    .getByTestId("impact-sub-skills")
    .locator(".ant-table-row .ant-table-cell:first-child"); // virtual rows are divs
  await expect(ids).toHaveText(["MS01.2", "MS01.4"]); // the same skill_ids as the API answer
  await expect(panel.locator('[data-item="F-OTP"]')).toBeVisible();
  await expect(panel.locator('[data-section="overview"]')).toHaveCount(0); // unchanged: not listed
  await expect(page.getByTestId("impact-macros")).toContainText("MS01");
  expect(f.impactAsked).toContain(4);
  // and the confirmation names the scope change before anything is signed
  await approveButton(page).click();
  await expect(page.getByTestId("approve-impact")).toContainText(
    "Scope change from v3: 2 Sub-Skills to rebuild",
  );
});

test("e7_3_t08 create new version: a reason, then v4 opens in the editor with its impact", async ({
  page,
  baseURL,
}) => {
  const f = fake(approvedV3(), { pipeline: "SOW_APPROVED", impact: impactOf([]) });
  await open(page, baseURL!, f);
  await expect(page.getByTestId("sow-locked")).toBeVisible();
  await page.getByRole("button", { name: "Create new version" }).click();
  const dialog = page.getByRole("dialog", { name: "Create a new version" });
  const create = dialog.getByRole("button", { name: "Create" });
  await expect(create).toBeDisabled(); // no reason yet
  const reason = dialog.getByLabel("Reason for the scope change");
  await reason.fill("x".repeat(501));
  await expect(dialog.getByTestId("reason-problem")).toHaveText("At most 500 characters");
  await expect(create).toBeDisabled();
  await reason.fill("Add OTP device limits");
  await create.click();
  await expect(page.getByText("v4 created: edit it, then approve it.")).toBeVisible();
  expect(f.newVersions).toEqual([{ reason: "Add OTP device limits" }]);
  await expect(page.getByRole("heading", { name: "SOW v4" })).toBeVisible();
  await expect(page.getByTestId("sow-screen").locator('[data-status="IN_REVIEW"]')).toBeVisible();
  await expect(page.getByTestId("sow-locked")).toHaveCount(0);
  await expect(editor(page)).toHaveAttribute("contenteditable", "true");
  await expect(page.getByTestId("impact-none")).toHaveText("No Sub-Skills need rebuild");
});

test("e7_3 a draft already open: Create new version is off", async ({
  page,
  baseURL,
}) => {
  const f = fake(draftV4(), { impact: impactOf([]) });
  await open(page, baseURL!, f);
  await page.getByRole("tab", { name: "Versions (4)" }).click();
  await page
    .getByTestId("sow-versions")
    .locator(".ant-table-row")
    .filter({ hasText: "v3" })
    .click();
  await expect(page.getByTestId("sow-locked")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create new version" })).toBeDisabled();
  expect(f.newVersions).toEqual([]);
});

test("e7_3 an impact of 300 Sub-Skills is virtualized", async ({ page, baseURL }) => {
  const many = Array.from({ length: 300 }, (_, i) => ({
    skill_id: `MS01.${i + 1}`,
    reason: "cites F-OTP",
  }));
  await open(page, baseURL!, fake(draftV4(), { impact: impactOf(many) }));
  await page.getByRole("tab", { name: "Impact" }).click();
  await expect(page.getByTestId("sow-impact")).toContainText("300 Sub-Skills to rebuild");
  const rows = page.getByTestId("impact-sub-skills").locator(".ant-table-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeLessThan(60);
});

test("e7_3 impact text is rendered as text", async ({ page, baseURL }) => {
  const evil = [{ skill_id: "MS01.2", reason: "<img src=x onerror=alert(1)>cites F-OTP" }];
  await open(page, baseURL!, fake(draftV4(), { impact: impactOf(evil) }));
  await page.getByRole("tab", { name: "Impact" }).click();
  await expect(page.getByTestId("impact-sub-skills")).toContainText("<img src=x onerror");
  await expect(page.getByTestId("sow-impact").locator("img")).toHaveCount(0);
});
