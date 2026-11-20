import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

// MUI-4 (#783): the approved masters as AG Grid screens on the BAM API. Like admin-masters.spec.ts, the API is
// faked at the proxy (`/api/proxy/api/v1/**`) on the default `next dev` project. The fake keeps real versions and
// row versions, so If-Match, the stale case and retire behave like the server.
// Screenshots: set E2E_SHOTS to a folder to also write the light / dark / 390 px set.

const signInAs = (page: Page, roles: string, baseURL: string) =>
  page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);

const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({
    status,
    headers,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });

const col = (header: string, field: string) => ({ header, field, description: `${header} help` });
const info = (master_type: string, tab: string, columns: ReturnType<typeof col>[]) => ({
  master_type,
  tab,
  editable: master_type !== "FILETYPE",
  row_label_prefix: tab,
  columns,
  payload_schema: {},
});

const LAYOUT = {
  types: [
    info("ORGANIZATION", "Organizations", [
      col("Org ID", "org_id"),
      col("Company / Type", "company_type"),
      col("Location", "location"),
      col("Data Sources", "data_sources"),
    ]),
    info("RULES", "Rules", [col("Rule ID", "rule_id"), col("Rule", "rule_type_description")]),
    info("AGENTS", "Agents", [
      col("Agent ID", "agent_id"),
      col("Agent Name", "agent_name"),
      col("Kind", "kind"),
      col("Tools", "tools"),
    ]),
    info("SKILLS", "Skills", [
      col("Skill ID", "skill_id"),
      col("Skill", "skill"),
      col("I · Instruction", "instruction"),
      col("References", "references"),
    ]),
    info("TOOLING", "Tool Factory", [
      col("Tooling ID", "tooling_id"),
      col("Tool Name", "tool_name"),
      col("Category", "category"),
      col("BuLLM badge", "bullm_badge"),
    ]),
    info("FILETYPE", "Filetype", [col("File Format", "file_format")]),
  ],
  readme: { title: "BuLLM - Admin Masters (BAM)", rows: [["1. STRUCTURE", null, null, null]] },
};

type Row = {
  id: string;
  master_type: string;
  stable_id: string;
  status: string;
  version: number;
  rv: number;
  payload: Record<string, string>;
};

let seq = 0;
const row = (type: string, id: string, payload: Record<string, string>): Row => ({
  id: `0190f3a2-7b1c-7d4e-8f00-${String(++seq).padStart(12, "0")}`,
  master_type: type,
  stable_id: id,
  status: "PROPOSED",
  version: 1,
  rv: 1,
  payload,
});

const seed = (): Row[] => [
  row("ORGANIZATION", "ORG-1", {
    org_id: "ORG-1",
    company_type: "Acme Corp",
    location: "Pune",
    data_sources: "https://example.test/sheet",
  }),
  row("ORGANIZATION", "ORG-2", { org_id: "ORG-2", company_type: "Beta Ltd", location: "Delhi" }),
  row("SKILLS", "SK-01", {
    skill_id: "SK-01",
    skill: "Zeta intake",
    instruction: "Read the RFQ",
    references: "SOP-1, SOP-2",
  }),
  row("SKILLS", "SK-02", { skill_id: "SK-02", skill: "Alpha quote", instruction: "Price it" }),
  row("TOOLING", "TOOL-1", {
    tooling_id: "TOOL-1",
    tool_name: "Parser",
    category: "API",
    bullm_badge: "CREDIBLE",
  }),
  row("TOOLING", "TOOL-2", { tooling_id: "TOOL-2", tool_name: "Bridge", category: "MCP" }),
  row("AGENTS", "AG-01", { agent_id: "AG-01", agent_name: "Scout", kind: "Agent" }),
  row("AGENTS", "AS-01", { agent_id: "AS-01", agent_name: "Helper", kind: "Assistant" }),
];

const entryOut = (r: Row) => ({
  id: r.id,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  project_id: null,
  master_type: r.master_type,
  stable_id: r.stable_id,
  row_label: `${r.master_type} #${r.stable_id}`,
  status: r.status,
  source_kind: "ADMIN_ENTRY",
  version: r.version,
  payload: r.payload,
  created_at: "2026-10-22T10:00:00Z",
  updated_at: "2026-10-22T10:00:00Z",
});

async function fakeMasters(page: Page, opts: { error?: boolean } = {}) {
  const rows = seed();
  const log: { method: string; path: string; ifMatch?: string; body?: unknown }[] = [];
  await page.route("**/api/proxy/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    const method = req.method();
    if (path === "/admin/master-types") return json(route, LAYOUT);
    if (path === "/admin/filetypes") return json(route, []);
    if (path.startsWith("/admin/master-documents")) return json(route, { items: [] });
    const m = /^\/admin\/masters\/(\w+)(?:\/([\w-]+))?(?:\/(\w+))?$/.exec(path);
    if (!m) return json(route, { title: "Not found", status: 404 }, 404);
    const [type, id, action] = [m[1]!, m[2], m[3]];
    if (method !== "GET")
      log.push({
        method,
        path,
        ifMatch: req.headers()["if-match"],
        body: req.postDataJSON() as unknown,
      });
    if (!id && method === "GET") {
      if (opts.error)
        return json(route, { title: "Boom", status: 500, detail: "The database is down" }, 500);
      const items = rows.filter((r) => r.master_type === type && r.status !== "RETIRED");
      return json(route, { items: items.map(entryOut), next_cursor: null });
    }
    if (id === "bulk") {
      const n = (req.postDataJSON() as { rows: unknown[] }).rows.length;
      for (let i = 0; i < n; i++) {
        const sid = `NEW-${rows.length + 1}`;
        rows.push(
          row(type, sid, {
            [LAYOUT.types.find((t) => t.master_type === type)!.columns[0]!.field]: sid,
          }),
        );
      }
      return json(route, {
        applied: true,
        created: [],
        errors: [],
        conflicts: [],
        skipped_exact: 0,
      });
    }
    const target = rows.find((r) => r.id === id && r.master_type === type);
    if (!target) return json(route, { title: "Not found", status: 404 }, 404);
    if (method === "GET") return json(route, entryOut(target), 200, { etag: `"${target.rv}"` });
    if (req.headers()["if-match"] !== `"${target.rv}"`)
      return json(route, { title: "Stale", status: 412, code: "VERSION_CONFLICT" }, 412);
    if (action === "retire") target.status = "RETIRED";
    else target.payload = (req.postDataJSON() as { payload: Record<string, string> }).payload;
    target.version += action === "retire" ? 0 : 1;
    target.rv += 1;
    return json(route, entryOut(target), action === "retire" ? 200 : 201);
  });
  return { rows, log };
}

// A narrow screen folds the tabs that don't fit into antd's "more" menu.
const pickTab = async (page: Page, name: string) => {
  const tab = page.getByRole("tab", { name, exact: true });
  const more = page.locator(".ant-tabs-nav-more");
  const box = await tab.boundingBox();
  const bar = await page.locator(".ant-tabs-nav-wrap").boundingBox();
  const fits = box && bar && box.x >= bar.x && box.x + box.width <= bar.x + bar.width - 48;
  if (!fits && (await more.isVisible())) {
    await more.click();
    await page.locator(".ant-tabs-dropdown-menu-item", { hasText: name }).click();
  } else await tab.click();
};
// The grid is a lazy chunk: the first `next dev` compile can take a while.
const openTab = async (page: Page, name: string) => {
  await pickTab(page, name);
  await expect(page.locator(".ag-root").first()).toBeVisible({ timeout: 30_000 });
  return page.getByRole("tabpanel");
};
const cell = (page: Page, id: string, colId: string): Locator =>
  page.locator(`.ag-row[row-id$="${id}"] [col-id="${colId}"]`).first();
const rowOf = (rows: Row[], stable: string) => rows.find((r) => r.stable_id === stable)!;
const rowId = (rows: Row[], stable: string) => rowOf(rows, stable).id;

test.use({ actionTimeout: 15_000 });
test.beforeEach(async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
});

test("mui4_t13 each master renders as an AG Grid with its columns, chips and links", async ({
  page,
}) => {
  const { rows } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await expect(page.getByRole("tab")).toHaveText([
    "README",
    "Organizations",
    "Rules",
    "Skills",
    "Tool Factory",
    "Documents",
    "Filetype",
  ]);
  await openTab(page, "Organizations");
  await expect(page.locator(".ag-header-cell-text", { hasText: "Company / Type" })).toBeVisible();
  await expect(cell(page, rowId(rows, "ORG-1"), "company_type")).toHaveText("Acme Corp");
  await expect(page.getByRole("link", { name: "https://example.test/sheet" })).toBeVisible();

  await openTab(page, "Skills");
  await expect(cell(page, rowId(rows, "SK-01"), "references").getByText("SOP-2")).toBeVisible();

  await openTab(page, "Tool Factory");
  await expect(page.getByLabel("BuLLM CREDIBLE")).toBeVisible();
  await page.getByRole("group", { name: "Category" }).getByText("MCP").click();
  await expect(cell(page, rowId(rows, "TOOL-2"), "tool_name")).toBeVisible();
  await expect(page.locator(`.ag-row[row-id$="${rowId(rows, "TOOL-1")}"]`)).toHaveCount(0);
  await page.getByText("Assistants", { exact: true }).click();
  await expect(cell(page, rowId(rows, "AS-01"), "agent_name")).toHaveText("Helper");
  await expect(page.locator(`.ag-row[row-id$="${rowId(rows, "AG-01")}"]`)).toHaveCount(0);
});

test("mui4_t14 edit round trip: double-click, type, Ctrl+Enter creates a new version with If-Match", async ({
  page,
}) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Organizations");
  await cell(page, rowId(rows, "ORG-1"), "location").dblclick();
  const editor = page.locator(".ag-large-text-input textarea, .ag-large-text textarea").first();
  await editor.fill("Chennai");
  await editor.press("Control+Enter");
  await expect(page.getByText("Saved ORG-1 (version 2)")).toBeVisible();
  await expect(cell(page, rowId(rows, "ORG-1"), "location")).toHaveText("Chennai");
  const post = log.find((l) => l.method === "POST")!;
  expect(post.path).toBe(`/admin/masters/ORGANIZATION/${rowId(rows, "ORG-1")}/versions`);
  expect(post.ifMatch).toBe('"1"');
  expect(rowOf(rows, "ORG-1").payload.company_type).toBe("Acme Corp"); // the rest of the row is kept
});

test("mui4_t15 stale row: someone else saved first, the warning shows and the grid reloads", async ({
  page,
}) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Skills");
  const target = rowOf(rows, "SK-02");
  Object.assign(target, { version: 5, rv: 9 });
  target.payload = { ...target.payload, skill: "Changed elsewhere" };
  await cell(page, target.id, "skill").click(); // Skills: single-click edit
  const editor = page.locator(".ag-large-text-input textarea, .ag-large-text textarea").first();
  await editor.fill("My edit");
  await editor.press("Control+Enter");
  await expect(page.getByText(/changed since you opened it/)).toBeVisible();
  await expect(cell(page, target.id, "skill")).toHaveText("Changed elsewhere");
  expect(log.filter((l) => l.method === "POST")).toEqual([]);
});

test("mui4_t16 retire asks first, then the row leaves the list", async ({ page }) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Skills");
  await page.getByRole("button", { name: "Retire SK-01" }).click();
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByText("Retired SK-01")).toBeVisible();
  await expect(page.locator(`.ag-row[row-id$="${rowId(rows, "SK-01")}"]`)).toHaveCount(0);
  expect(log.find((l) => l.method === "POST")?.path).toContain("/retire");
  expect(rowOf(rows, "SK-01").status).toBe("RETIRED");
});

test("mui4_t17 sorting works: a header click orders the rows (B3)", async ({ page }) => {
  const { rows } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Skills");
  const names = () => page.locator('[col-id="skill"].ag-cell').allTextContents();
  await expect.poll(names).toEqual(["Zeta intake", "Alpha quote"]);
  await page.locator(".ag-header-cell-text", { hasText: /^Skill$/ }).click();
  await expect.poll(names).toEqual(["Alpha quote", "Zeta intake"]);
  expect(rows.length).toBeGreaterThan(0);
});

test("mui4_t18 Enter adds a line in the multiline editor and does not close it (B2)", async ({
  page,
}) => {
  const { rows, log } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Skills");
  await cell(page, rowId(rows, "SK-02"), "instruction").click();
  const editor = page.locator(".ag-large-text-input textarea, .ag-large-text textarea").first();
  await editor.fill("line one");
  await editor.press("Enter");
  await editor.type("line two");
  await expect(editor).toBeVisible();
  expect(log).toEqual([]);
  await editor.press("Control+Enter");
  await expect(page.getByText("Saved SK-02 (version 2)")).toBeVisible();
  expect(rowOf(rows, "SK-02").payload.instruction).toBe("line one\nline two");
});

test("mui4_t21 search with facets, Add N blank rows and the footer hint", async ({ page }) => {
  const { rows } = await fakeMasters(page);
  await page.goto("/admin/masters/tenant");
  await openTab(page, "Organizations");
  await page.getByRole("combobox", { name: "Search Organizations" }).fill("beta");
  await expect(page.getByText("1 of 2 rows")).toBeVisible();
  await page.getByRole("combobox", { name: "Search Organizations" }).fill("");
  await page.getByRole("combobox", { name: "Filter by Location" }).click();
  await page.locator(".ant-select-item-option", { hasText: "Pune" }).click();
  await expect(page.getByText("1 of 2 rows")).toBeVisible();
  await expect(page.getByText(/Ctrl\+Enter saves/)).toBeVisible();
  await page.getByRole("button", { name: /Add 5 blank rows/ }).click();
  await expect(page.getByText("Added 5 blank rows")).toBeVisible();
  expect(rows.filter((r) => r.master_type === "ORGANIZATION")).toHaveLength(7);
});

test("mui4_t22 the grid shows the problem+json detail when the list fails", async ({ page }) => {
  await fakeMasters(page, { error: true });
  await page.goto("/admin/masters/tenant");
  await pickTab(page, "Skills");
  // the list query retries three times before it gives up
  await expect(page.getByText(/The database is down/)).toBeVisible({ timeout: 20_000 });
});

for (const width of [320, 390]) {
  test(`mui4_t19 no horizontal overflow at ${width}px`, async ({ page }) => {
    await fakeMasters(page);
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/admin/masters/tenant");
    for (const tab of ["Organizations", "Skills", "Tool Factory"]) {
      await openTab(page, tab);
      // Name the widest offenders, so a failure says what overflows.
      const offenders = await page.evaluate(() => {
        const w = document.documentElement.clientWidth;
        return [...document.querySelectorAll("body *")]
          .filter((e) => {
            if (e.getBoundingClientRect().right <= w + 1) return false;
            // clipped by a scroll container (the tab bar, the grid): not page overflow
            for (let a = e.parentElement; a; a = a.parentElement)
              if (getComputedStyle(a).overflowX !== "visible") return false;
            return true;
          })
          .slice(0, 6)
          .map((e) => `${e.tagName}.${String((e as HTMLElement).className).slice(0, 50)}`);
      });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${tab} at ${width}px: ${offenders.join(" | ")}`).toBeLessThanOrEqual(0);
    }
  });
}

test("mui4_t20 an FDE gets Forbidden and no masters request", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/masters/tenant");
  await expect(page.getByText("Forbidden")).toBeVisible();
  expect(apiCalls).toEqual([]);
});

test("mui4 screenshots: every grid, light, dark and 390 px (only with E2E_SHOTS)", async ({
  page,
}) => {
  const dir = process.env.E2E_SHOTS;
  test.skip(!dir, "E2E_SHOTS names the folder");
  mkdirSync(dir!, { recursive: true });
  await fakeMasters(page);
  const shoot = async (name: string, theme: "light" | "dark", width: number) => {
    await page.addInitScript(
      ([k, v]) => window.localStorage.setItem(k!, v!),
      ["sbam-admin-theme", theme],
    );
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/masters/tenant");
    for (const [tab, file] of [
      ["Organizations", "organisations"],
      ["Skills", "skills"],
      ["Tool Factory", "tool-factory"],
    ] as const) {
      await openTab(page, tab);
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(dir!, `${file}-${name}.png`) });
    }
    await page.getByText("Assistants", { exact: true }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(dir!, `assistants-${name}.png`) });
  };
  await shoot("light", "light", 1280);
  await shoot("dark", "dark", 1280);
  await shoot("390", "light", 390);
});
