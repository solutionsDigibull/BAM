import { expect, test, type Page, type Route } from "@playwright/test";

// E3.10 Admin Masters. Like admin-projects.spec.ts, the API is faked at the proxy (`/api/proxy/api/v1/**`)
// on the default `next dev` project; a stack run (E2E_BASE_URL) exercises the real one.

const signInAs = async (page: Page, roles: string, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: roles, url: baseURL }]);
};

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    body: JSON.stringify(body),
  });

const column = (header: string, field: string, description: string) => ({
  header,
  field,
  description,
});
const info = (
  master_type: string,
  tab: string,
  editable: boolean,
  columns: ReturnType<typeof column>[],
) => ({
  master_type,
  tab,
  editable,
  row_label_prefix: tab,
  columns,
  payload_schema: {},
});

const LAYOUT = {
  types: [
    info("ORGANIZATION", "Organization", true, [column("Org Name", "org_name", "Company Name")]),
    info("RULES", "Rules", true, [
      column("Domain Rule", "domain_rule", "Live Costing & Margin Price Break Sheet"),
      column("PCB Panelization", "pcb_panelization", "Base PCB Dimensions & Yields"),
      column("Misc", "misc", "Anything else"),
    ]),
    info("AGENTS", "Agents", true, [column("Agent Name", "agent_name", "Name of the Agent")]),
    info("SKILLS", "Skills", true, [column("Skill", "skill", "Skills required for the project")]),
    info("TOOLING", "Tooling", true, [column("Category", "category", "Add-on category")]),
    info("FILETYPE", "Filetype", false, [column("File Format", "file_format", "Input Filetype")]),
  ],
  readme: {
    title: "BuLLM - Admin Masters (BAM) - version 0.1",
    rows: [["1. STRUCTURE", null, null, null]],
  },
};

const RULE_BOM = {
  id: "0190f3a2-7b1c-7d4e-8f00-00000000e001",
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  project_id: null,
  master_type: "RULES",
  stable_id: "RULE-BOM",
  row_label: "Rule #2",
  status: "PROPOSED",
  source_kind: "ADMIN_ENTRY",
  version: 1,
  payload: {
    domain_rule: "RULE-BOM: EMS; Scrub BOM / CBOM. Policy: compare BOMs",
    misc: "Status: Proposed",
  },
  created_at: "2026-10-22T10:00:00Z",
  updated_at: "2026-10-22T10:00:00Z",
};

const FILETYPES = [1, 2, 3].map((n) => ({
  row_label: `Filetype #${n}`,
  file_format: [".cpp", ".pdf", ".msg"][n - 1],
  tier: n,
  purpose: "Expose native application logic",
  rag_ingestion: "Prebundle a parser",
  example: "https://example.test/file",
  mvp_enabled: n === 2,
  loader: n === 2 ? "pdf-text" : null,
  extensions: [],
  intent_class: "code",
  default_route: "knowledge",
}));

const report = (mode: string, applied: boolean) => ({
  mode,
  applied,
  sha256: `sha256:${"a".repeat(64)}`,
  tabs: [
    { name: "Organization", created: 0, updated: 0, unchanged: 0, missing: 0, errors: [] },
    { name: "Rules", created: 1, updated: 0, unchanged: 0, missing: 0, errors: [] },
    { name: "Filetype", created: 0, updated: 0, unchanged: 43, missing: 0, errors: [] },
  ],
  stable_ids: ["RULE-BOM"],
  statuses: { PROPOSED: 1 },
});

/** The masters API in memory; `onImport` answers POST /admin/masters/import by mode. */
async function fakeMasters(
  page: Page,
  onImport: (mode: string) => { status?: number; body: unknown },
) {
  const rules: unknown[] = [];
  const modes: string[] = [];
  await page.route("**/api/proxy/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    if (path === "/admin/master-types") return json(route, LAYOUT);
    if (path === "/admin/filetypes") return json(route, FILETYPES);
    if (req.method() === "POST" && path === "/admin/masters/import") {
      const mode = /name="mode"\r\n\r\n([\w-]+)/.exec(req.postData() ?? "")?.[1] ?? "";
      modes.push(mode);
      const reply = onImport(mode);
      if (mode === "upsert" && (reply.status ?? 200) === 200) rules.push(RULE_BOM);
      return json(route, reply.body, reply.status);
    }
    if (path === "/admin/masters/RULES") return json(route, { items: rules, next_cursor: null });
    if (/^\/admin\/masters\/\w+$/.test(path)) return json(route, { items: [], next_cursor: null });
    return json(route, { title: "Not found", status: 404 }, 404);
  });
  return { modes };
}

const WORKBOOK = {
  name: "BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx",
  mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  buffer: Buffer.from("PK fake xlsx; the API is faked"),
};

async function chooseAndValidate(page: Page) {
  await page.getByRole("button", { name: "Import workbook" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(WORKBOOK);
  await dialog.getByRole("button", { name: "Validate" }).click();
  return dialog;
}

test("e3_10 FDE on /admin/masters gets Forbidden and no data request", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-fde", baseURL!);
  const apiCalls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/proxy/") && apiCalls.push(r.url()));
  await page.goto("/admin/masters");
  await expect(page.getByText("Forbidden")).toBeVisible();
  expect(apiCalls).toEqual([]);
});

test("e3_10 six tabs; Filetype has no edit controls", async ({ page, baseURL }) => {
  await signInAs(page, "sbam-admin", baseURL!);
  await fakeMasters(page, () => ({ body: {} }));
  await page.goto("/admin/masters");
  await expect(page.getByRole("tab")).toHaveText([
    "README",
    "Organization",
    "Rules",
    "Agents",
    "Skills",
    "Filetype",
  ]);
  await page.getByRole("tab", { name: "Filetype" }).click();
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByText("Filetype #1", { exact: true })).toBeVisible();
  await expect(panel.getByText("pdf-text")).toBeVisible();
  await expect(panel.getByRole("button", { name: /Add Row|Edit/ })).toHaveCount(0);
});

test("e3_10_t01 import: validate -> preview -> apply, then the entries are visible", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const { modes } = await fakeMasters(page, (mode) => ({ body: report(mode, mode === "upsert") }));
  await page.goto("/admin/masters");
  await page.getByRole("tab", { name: "Rules" }).click();
  await expect(page.getByText(/No Rules entries yet/)).toBeVisible();

  const dialog = await chooseAndValidate(page);
  await expect(dialog.getByText(/The workbook is valid/)).toBeVisible();
  await expect(dialog.getByRole("row", { name: /Rules\s+1/ })).toBeVisible();
  await dialog.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText("Imported: 1 created, 0 updated")).toBeVisible();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("RULE-BOM", { exact: true })).toBeVisible(); // stable-ID chip
  expect(modes).toEqual(["validate-only", "upsert"]);
});

test("e3_10_t02 reordered Skills columns: diff table, Apply disabled", async ({
  page,
  baseURL,
}) => {
  await signInAs(page, "sbam-admin", baseURL!);
  const { modes } = await fakeMasters(page, () => ({
    status: 422,
    body: {
      type: "about:blank",
      title: "The workbook does not have the BAM 0.2 structure",
      status: 422,
      detail: "Use the published template; columns and tabs are never mapped automatically.",
      code: "STRUCTURE_MISMATCH",
      errors: [
        {
          path: "Skills!row2:col1",
          message: "expected 'Documents', found 'RFQ / Inquiries'",
          diff: {
            tab: "Skills",
            expected: "Documents",
            actual: "RFQ / Inquiries",
            kind: "reordered",
          },
        },
        {
          path: "Skills!row2:col2",
          message: "expected 'RFQ / Inquiries', found 'Documents'",
          diff: {
            tab: "Skills",
            expected: "RFQ / Inquiries",
            actual: "Documents",
            kind: "reordered",
          },
        },
      ],
    },
  }));
  await page.goto("/admin/masters");
  const dialog = await chooseAndValidate(page);
  await expect(dialog.getByText("The workbook does not have the BAM 0.2 structure")).toBeVisible();
  await expect(dialog.getByText("reordered")).toHaveCount(2);
  await expect(dialog.getByRole("button", { name: "Apply" })).toBeDisabled();
  expect(modes).toEqual(["validate-only"]);
});
