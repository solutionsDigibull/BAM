import { expect, type Page, type Route } from "@playwright/test";

// A fake sbam-api for the Admin Masters specs (#784): masters with real versions and row versions (so
// If-Match, the stale case and retire behave like the server), bulk, master lists and masters scans.
// The API is faked at the proxy (`/api/proxy/api/v1/**`) on the default `next dev` project.

export const signInAs = (page: Page, roles: string, baseURL: string) =>
  page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);

export const json = (
  route: Route,
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) =>
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

export const LAYOUT = {
  types: [
    info("ORGANIZATION", "Organizations", [
      col("Org ID", "org_id"),
      col("Company / Type", "company_type"),
      col("Location", "location"),
    ]),
    info("RULES", "Rules", [
      col("Rule ID", "rule_id"),
      col("Rule / Type / Description", "rule_type_description"),
      col("Status", "record_status"),
      col("Skill ref", "skill_ref"),
    ]),
    info("AGENTS", "Agents", [
      col("Agent ID", "agent_id"),
      col("Agent Name", "agent_name"),
      col("Kind", "kind"),
      col("Tools", "tools"),
    ]),
    info("SKILLS", "Skills", [
      col("Skill ID", "skill_id"),
      col("Skill", "skill"),
      col("N · Next", "next"),
    ]),
    info("TOOLING", "Tool Factory", [
      col("Tooling ID", "tooling_id"),
      col("Tool Name", "tool_name"),
      col("Category", "category"),
    ]),
    info("FILETYPE", "Filetype", [col("File Format", "file_format")]),
  ],
  readme: { title: "BuLLM - Admin Masters (BAM)", rows: [["1. STRUCTURE", null, null, null]] },
};

export type Row = {
  id: string;
  master_type: string;
  stable_id: string;
  status: string;
  version: number;
  rv: number;
  payload: Record<string, unknown>;
};

let seq = 0;
export const row = (type: string, id: string, payload: Record<string, unknown>): Row => ({
  id: `0190f3a2-7b1c-7d4e-8f00-${String(++seq).padStart(12, "0")}`,
  master_type: type,
  stable_id: id,
  status: "PROPOSED",
  version: 1,
  rv: 1,
  payload,
});

export const seedRows = (): Row[] => [
  row("SKILLS", "SK-01", { skill_id: "SK-01", skill: "Intake", next: "SK-02" }),
  row("SKILLS", "SK-02", { skill_id: "SK-02", skill: "Quote", next: "" }),
  row("RULES", "RULE-1", {
    rule_id: "RULE-1",
    rule_type_description: "BOM scrub. Policy: compare the BOMs",
    record_status: "Proposed",
    skill_ref: "SK-01",
    prompts: { system: "Be exact", micro: [{ name: "m1", text: "check" }] },
  }),
  row("RULES", "RULE-2", {
    rule_id: "RULE-2",
    rule_type_description: "Margin. Estimation: price break",
    record_status: "Approved",
    skill_ref: "SK-02",
  }),
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

export type Logged = { method: string; path: string; ifMatch?: string; body?: unknown };

export async function fakeMasters(page: Page, opts: { error?: boolean; rows?: Row[] } = {}) {
  const rows = opts.rows ?? seedRows();
  const log: Logged[] = [];
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
    if (!id && method === "POST") {
      const payload = (req.postDataJSON() as { payload: Record<string, unknown> }).payload;
      const idField = LAYOUT.types.find((t) => t.master_type === type)!.columns[0]!.field;
      const sid = String(payload[idField] || `NEW-${rows.length + 1}`);
      const created = row(type, sid, { ...payload, [idField]: sid });
      rows.push(created);
      return json(route, entryOut(created), 201);
    }
    const target = rows.find((r) => r.id === id && r.master_type === type);
    if (!target) return json(route, { title: "Not found", status: 404 }, 404);
    if (method === "GET") return json(route, entryOut(target), 200, { etag: `"${target.rv}"` });
    if (req.headers()["if-match"] !== `"${target.rv}"`)
      return json(route, { title: "Stale", status: 412, code: "VERSION_CONFLICT" }, 412);
    if (action === "retire") target.status = "RETIRED";
    else target.payload = (req.postDataJSON() as { payload: Record<string, unknown> }).payload;
    target.version += action === "retire" ? 0 : 1;
    target.rv += 1;
    return json(route, entryOut(target), action === "retire" ? 200 : 201);
  });
  return { rows, log };
}

/** The grid is a lazy chunk and a narrow screen folds the tabs: focus + Enter works for any tab. */
export const openTab = async (page: Page, name: string, grid = true) => {
  const tab = page.getByRole("tab", { name, exact: true });
  await tab.focus();
  await page.keyboard.press("Enter");
  if (grid) await expect(page.locator(".ag-root").first()).toBeVisible({ timeout: 30_000 });
  return page.getByRole("tabpanel");
};

/** The elements that overflow the page (not clipped by a scroll container), named for the failure. */
export const pageOverflow = (page: Page) =>
  page.evaluate(() => {
    const w = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")]
      .filter((e) => {
        if (e.getBoundingClientRect().right <= w + 1) return false;
        for (let a = e.parentElement; a; a = a.parentElement)
          if (getComputedStyle(a).overflowX !== "visible") return false;
        return true;
      })
      .slice(0, 6)
      .map((e) => `${e.tagName}.${String((e as HTMLElement).className).slice(0, 50)}`);
    return { by: document.documentElement.scrollWidth - w, offenders };
  });
