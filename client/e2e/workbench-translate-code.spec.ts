import { expect, test, type Page, type Route } from "@playwright/test";

// #841: the new workbench's Translate step shows the whole workflow's stored code, read only (the #763 API).
// The API is faked at the proxy (no sbam-api here). Set UI_SHOTS_DIR to save a screenshot of each state for
// comparison with the BAM_UI reference.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const API = "**/api/proxy/api/v1";
const SHA = "b".repeat(64);
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
const problem = (route: Route, status: number, title: string, detail?: string) =>
  json(route, { type: "about:blank", title, status, detail }, status);

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "WORKFLOW_CREATED",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};
const workflow = {
  id: WORKFLOW,
  project_id: PROJECT,
  sow_version_id: "v1",
  name: "Onboarding",
  version: "1.0.0",
  state: "BUILDING",
  created_at: "2026-11-10T10:00:00Z",
};
const finding = { gate: "static", code: "S101", file: "skill.py", line: 2, message: "assert used" };
const TREE = {
  workflow_id: WORKFLOW,
  groups: [
    {
      kind: "sub_skill",
      group_id: "s1",
      label: "MS01.1 Intake",
      build_no: 1,
      cycle: 1,
      attempt: 0,
      status: "PASSED",
      files: [
        { path: "skill.py", sha256: SHA, tag: "generated", findings: [finding] },
        { path: "vendor/acme-tools@abc1234/lib.py", sha256: SHA, tag: "reused", findings: [] },
      ],
      gates: [
        { gate: "ast", status: "PASSED" },
        { gate: "import", status: "FAILED" },
      ],
      findings: [],
    },
    {
      kind: "workflow",
      group_id: "workflow",
      label: "Workflow",
      status: null,
      files: [
        {
          path: "patches/acme-tools@abc1234/0001-fix.patch",
          sha256: SHA,
          tag: "customized",
          findings: [],
        },
        { path: "manifest.json", sha256: SHA, tag: "generated", findings: [] },
      ],
      gates: [],
      findings: [],
    },
  ],
};
const CONTENT: Record<string, string> = {
  "skill.py": "def run(item):\n    assert item\n    return item\n",
  "vendor/acme-tools@abc1234/lib.py": "def helper():\n    return 1\n",
  "patches/acme-tools@abc1234/0001-fix.patch":
    "--- a/lib.py\n+++ b/lib.py\n@@ -1 +1 @@\n-return 1\n+return 2\n",
  "manifest.json": '{\n  "name": "Onboarding"\n}\n',
};

async function open(page: Page, baseURL: string, code: (route: Route) => unknown) {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  // Calls no test fakes answer 404 (with no sbam-api the proxy would answer 401 and sign the user out, #742).
  await page.route(`${API}/**`, (r) => problem(r, 404, "Not Found"));
  await page.route(
    (u) => u.pathname.endsWith("/api/proxy/api/v1/projects"),
    (r) => json(r, { items: [project] }),
  );
  await page.route(`${API}/projects/${PROJECT}/documents**`, (r) =>
    json(r, { items: [], next_cursor: null }),
  );
  await page.route(`${API}/projects/${PROJECT}/pipeline`, (r) =>
    json(r, { state: "WORKFLOW_CREATED", steps: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/knowledge`, (r) => json(r, { versions: [] }));
  await page.route(`${API}/projects/${PROJECT}/workflows`, (r) => json(r, { items: [workflow] }));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) => code(r));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code/files**`, (r) => {
    const q = new URL(r.request().url()).searchParams;
    const path = q.get("path") ?? "";
    const group = q.get("group") ?? "";
    const tag =
      TREE.groups.find((g) => g.group_id === group)?.files.find((f) => f.path === path)?.tag ??
      "generated";
    return json(r, {
      group_id: group,
      path,
      tag,
      sha256: SHA,
      content: CONTENT[path] ?? "",
      truncated: false,
      findings: path === "skill.py" ? [finding] : [],
    });
  });
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e841_loaded every file has its tag, the gates show, a finding links to its line, nothing can be edited", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, TREE));
  const preview = page.getByTestId("workflow-code");
  await expect(preview).toBeVisible();
  const nav = preview.getByRole("navigation", { name: "Workflow files" });
  await expect(nav.getByRole("button")).toHaveCount(4);
  await expect(nav.getByTitle("vendor/acme-tools@abc1234/lib.py")).toContainText("Reused");
  await expect(nav.getByTitle("patches/acme-tools@abc1234/0001-fix.patch")).toContainText(
    "Customized",
  );
  await expect(nav.getByTitle("skill.py")).toContainText("Generated");
  await expect(preview.getByRole("list", { name: "Gate results for MS01.1 Intake" })).toContainText(
    "import · FAILED",
  );
  const code = preview.getByRole("list", { name: "Content of skill.py" });
  await expect(code.getByRole("listitem")).toHaveCount(3);
  await preview.getByRole("button", { name: "line 2" }).click();
  await expect(code.locator("li.focus")).toContainText("assert item");
  await expect(preview.getByRole("textbox")).toHaveCount(0);
  await expect(preview.getByText("Read only")).toBeVisible();
  await shot(page, "1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "1c-loaded-wide");
  await page.setViewportSize({ width: 1280, height: 720 });
  await nav.getByTitle("manifest.json").click();
  await expect(preview.getByRole("list", { name: "Content of manifest.json" })).toContainText(
    '"name": "Onboarding"',
  );
  await shot(page, "1b-another-file");
});

test("e841_empty a workflow with no stored code says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) =>
    json(r, {
      workflow_id: WORKFLOW,
      groups: [
        { ...TREE.groups[0], files: [], gates: [] },
        { ...TREE.groups[1], files: [] },
      ],
    }),
  );
  await expect(page.getByText("No code stored yet")).toBeVisible();
  await shot(page, "2-empty");
});

test("e841_error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true; // stays down (also for the browser's own refetches) until Retry is clicked
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The database is down") : json(r, TREE),
  );
  await expect(page.getByText("The database is down")).toBeVisible();
  await expect(page.getByTestId("workflow-code")).toHaveCount(0);
  await shot(page, "3-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("workflow-code")).toBeVisible();
});

test("e841_forbidden another tenant or an unassigned FDE gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 404, "Not Found"));
  await expect(page.getByText("You can't see this workflow's code")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "4-forbidden");
});
