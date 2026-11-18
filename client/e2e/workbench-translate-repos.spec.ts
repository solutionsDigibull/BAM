import { expect, test, type Page, type Route } from "@playwright/test";

// #843: the new workbench's Translate step shows the repositories the workflow starts from, read only: the pinned
// commit, the fetcher's scan report (#755), the patches (#757) and the components. The API is faked at the proxy
// (no sbam-api here). Set UI_SHOTS_DIR to save a screenshot of each state for comparison with the BAM_UI reference.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const API = "**/api/proxy/api/v1";
const COMMIT = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678";
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
const TREE = { workflow_id: WORKFLOW, groups: [] };
const REPORT = {
  url: "https://github.com/acme/pcb-tools",
  host: "github.com",
  ref: "v2.4.0",
  commit: COMMIT,
  decision: "VENDORED",
  refusals: [],
  licence: { spdx: "MIT", source: "LICENSE", verdict: "ALLOWED", override: null },
  dependency_licences: [
    {
      ecosystem: "PyPI",
      name: "numpy",
      version: "2.1.0",
      spdx: "BSD-3-Clause",
      verdict: "ALLOWED",
    },
    {
      ecosystem: "PyPI",
      name: "readline-gpl",
      version: "8.2",
      spdx: "GPL-3.0",
      verdict: "REFUSED",
    },
  ],
  vulnerabilities: [
    {
      ecosystem: "PyPI",
      package: "pillow",
      version: "9.0.0",
      id: "CVE-2026-1111",
      summary: "Heap overflow in the image decoder",
      severity: "CRITICAL",
    },
    {
      ecosystem: "PyPI",
      package: "requests",
      version: "2.20.0",
      id: "CVE-2026-2222",
      summary: "Header injection",
      severity: "MEDIUM",
    },
  ],
  static_findings: [
    {
      tool: "bandit",
      rule: "B602",
      path: "pcb/run.py",
      line: 41,
      severity: "HIGH",
      message: "subprocess call with shell=True",
    },
    {
      tool: "pattern",
      rule: "install-hook",
      path: "setup.py",
      line: 0,
      severity: "MEDIUM",
      message: "runs code at install time",
    },
  ],
  clam_findings: [{ path: "tests/data/sample.bin", signature: "Test.Signature.UNOFFICIAL" }],
  files_scanned: 214,
  unsafe_paths: [],
  health: {
    last_commit_at: "2026-09-18T08:30:00Z",
    last_release_at: "2026-08-02T10:00:00Z",
    archived: false,
    flags: ["STALE"],
  },
  bundle_sha256: null,
  bundle_files: 0,
  notes: [],
};
const PATCH = {
  path: `patches/pcb-tools@${COMMIT}/0001-use-project-logger.patch`,
  sha256: "1".repeat(64),
  reason: "Use the project logger instead of print so the run is traceable",
  files: ["pcb/run.py"],
};
const SBOM = [
  { kind: "repository", name: "acme/pcb-tools", version: COMMIT, ecosystem: null, licence: "MIT" },
  {
    kind: "dependency",
    name: "numpy",
    version: "2.1.0",
    ecosystem: "PyPI",
    licence: "BSD-3-Clause",
  },
  {
    kind: "dependency",
    name: "readline-gpl",
    version: "8.2",
    ecosystem: "PyPI",
    licence: "GPL-3.0",
  },
];
const REPO = {
  url: REPORT.url,
  commit: COMMIT,
  licence: "MIT",
  repo_dir: `vendor/pcb-tools@${COMMIT}`,
  sub_skills: ["MS01.1_Intake", "MS01.2_Costing"],
  patches: [PATCH],
  report: REPORT,
  sbom: SBOM,
};
const DATA = (repositories: unknown[]) => ({ workflow_id: WORKFLOW, repositories });

async function open(page: Page, baseURL: string, repos: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) => json(r, TREE));
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/repositories`, (r) =>
    repos(r),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Repositories" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e843_loaded the report, the patches with their reasons and the components are shown, nothing can be edited", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, DATA([REPO])));
  const card = page.getByRole("region", { name: "acme/pcb-tools" });
  await expect(card).toBeVisible();
  await expect(card).toContainText("a1b2c3d4e5f6"); // the short commit
  await expect(card).toContainText("MIT · Allowed");
  await expect(card).toContainText("Needs attention");
  await expect(
    card.getByRole("list", { name: "Dependencies with a refused licence" }),
  ).toContainText("readline-gpl@8.2");
  const vulns = card.getByRole("list", { name: "Vulnerabilities" }).getByRole("listitem");
  await expect(vulns.first()).toContainText("CVE-2026-1111"); // CRITICAL before MEDIUM
  await expect(card.getByRole("list", { name: "Bandit and pattern findings" })).toContainText(
    "pcb/run.py:41",
  );
  await expect(card.getByRole("list", { name: "Quarantined files" })).toContainText(
    "Test.Signature.UNOFFICIAL",
  );
  await expect(card.getByRole("list", { name: "Patches" })).toContainText("Use the project logger");
  await card.getByText("Components (3)").click();
  await expect(card.getByRole("list", { name: "Components" })).toContainText("readline-gpl");
  await expect(page.locator(".rp-summary")).toContainText("1 repository");
  await expect(page.locator(".rp input, .rp textarea, .rp select, .rp form")).toHaveCount(0);
  await shot(page, "1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "1c-loaded-wide");
});

test("e843_no_report a repository without a stored report is listed with a notice, not an error", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) =>
    json(r, DATA([{ ...REPO, report: null, patches: [], sbom: SBOM.slice(0, 1) }])),
  );
  const card = page.getByRole("region", { name: "acme/pcb-tools" });
  await expect(card).toContainText("No scan report");
  await expect(card).toContainText("Certification needs one");
  await expect(card).toContainText("Used as is: no patch was applied.");
  await expect(page.getByText("Could not load the repositories")).toHaveCount(0);
  await shot(page, "1b-no-report");
});

test("e843_empty a workflow that uses no repository says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, DATA([])));
  await expect(
    page.getByText("No repository was used, or no scan report is attached"),
  ).toBeVisible();
  await shot(page, "2-empty");
});

test("e843_error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true; // stays down (also for the browser's own refetches) until Retry is clicked
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "Storage is unavailable") : json(r, DATA([REPO])),
  );
  await expect(page.getByText("Storage is unavailable")).toBeVisible();
  await shot(page, "3-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("region", { name: "acme/pcb-tools" })).toBeVisible();
});

test("e843_forbidden another tenant or an unassigned FDE gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 404, "Not Found"));
  await expect(page.getByText("You can't see this workflow's repositories")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "4-forbidden");
});
