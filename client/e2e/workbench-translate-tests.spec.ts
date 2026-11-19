import { expect, test, type Page, type Route } from "@playwright/test";

// #715: Translate shows the tests of one Sub-Skill, read only: the sandbox runs with each assertion's result, the harness
// trace of every case, the run's sandbox log as a download and the synthetic test data. The API is faked at the proxy.
// Set UI_SHOTS_DIR to save a screenshot of each state.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const RUN = "0190f3a2-7b1c-7d4e-8f00-0000000000e1";
const SANDBOX = "0190f3a2-7b1c-7d4e-8f00-0000000000f1";
const API = "**/api/proxy/api/v1";
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
const BUILDS = {
  workflow_id: WORKFLOW,
  workflow_state: "BUILDING",
  active: false,
  sub_skills: [
    {
      sub_skill_id: SUB,
      skill_id: "MS01.1_Intake",
      skill_name: "Intake the RFQ",
      state: "TESTING",
      stage: "run_sandbox_tests",
      attempt: 0,
      gates: {},
    },
  ],
};
const TESTS = {
  dataset: {
    version: 2,
    sha256: "a".repeat(64),
    synthetic: true,
    edited_by_fde: false,
    files_keys: [],
    created_at: "2026-11-18T10:00:00Z",
    cases: [
      {
        id: "c1",
        name: "small_rfq",
        input: { rows: 12, currency: "EUR" },
        expected_terminal: "SUCCESS",
        expected_output: { parts: 12 },
        kind: "PASS_FIRST",
      },
      {
        id: "c2",
        name: "rfq_without_quantities",
        input: { rows: 3, currency: "EUR" },
        expected_terminal: "ESCALATED",
        kind: "ESCALATION",
      },
      {
        id: "c3",
        name: "empty_rfq",
        input: { rows: 0 },
        expected_terminal: "NO_OP",
        kind: "NO_OP",
      },
    ],
  },
};
const RUNS = {
  runs: [
    {
      id: RUN,
      sub_skill_id: SUB,
      code_attempt_id: "c1",
      sandbox_run_id: SANDBOX,
      suite_sha256: "b".repeat(64),
      state: "FAIL",
      total: 4,
      passed: 3,
      failed: 1,
      results: [
        { case: "small_rfq", type: "terminal_state", passed: true },
        { case: "small_rfq", type: "output_schema", passed: true },
        { case: "rfq_without_quantities", type: "terminal_state", passed: true },
        {
          case: "empty_rfq",
          type: "terminal_state",
          passed: false,
          message: "ended SUCCESS, expected NO_OP",
        },
      ],
      created_at: "2026-11-18T11:00:00Z",
    },
  ],
};
const TRACES = {
  run_id: RUN,
  traces: [
    {
      case_id: "small_rfq",
      terminal: "SUCCESS",
      iterations: 1,
      handoff_rows: 1,
      ui_updates: ["done"],
      passes: [
        {
          pass: 1,
          fresh_state: {},
          action_output: {},
          check_output: {},
          action_ms: 14,
          check_ms: 4,
          ui_update: "12 parts read from the RFQ",
          gate_eval: { success: true, escalation: false },
        },
      ],
    },
    {
      case_id: "empty_rfq",
      terminal: null,
      iterations: 1,
      handoff_rows: 0,
      ui_updates: [],
      passes: [],
      error: {
        category: "ASSERTION_FAILED",
        pass: 1,
        step: "check",
        elapsed_ms: 6,
        message: "ended SUCCESS, expected NO_OP",
      },
    },
  ],
};
const root = `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}`;

async function open(page: Page, baseURL: string, runs: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/code`, (r) =>
    json(r, { workflow_id: WORKFLOW, groups: [] }),
  );
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/builds`, (r) =>
    json(r, BUILDS),
  );
  await page.route(`${root}/tests`, (r) => json(r, TESTS));
  await page.route(`${root}/test-runs`, (r) => runs(r));
  await page.route(`${root}/test-runs/${RUN}/traces`, (r) => json(r, TRACES));
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Tests", exact: true }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e715_tests loaded the run shows its state, the count and each case, the log is a download, nothing can be changed", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, RUNS));
  const runs = page.getByRole("list", { name: "Test runs" });
  await expect(runs).toContainText("3 of 4 passed");
  await expect(runs.getByRole("list", { name: "Results by case" })).toContainText(
    "ended SUCCESS, expected NO_OP",
  );
  const link = runs.getByRole("link", { name: /Download the sandbox log/ });
  await expect(link).toHaveAttribute(
    "href",
    `/api/proxy/api/v1/projects/${PROJECT}/workflows/${WORKFLOW}/sandbox-runs/${SANDBOX}/logs`,
  );
  await expect(page.getByRole("list", { name: "Test cases" })).toContainText("Escalates");
  await expect(page.locator(".tp textarea, .tp input")).toHaveCount(0);
  await shot(page, "tests-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "tests-1c-loaded-wide");
});

test("e715_tests traces show every pass and the failing step", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, RUNS));
  await page.getByRole("button", { name: "Show the traces" }).click();
  await expect(page.getByRole("region", { name: "Trace of small_rfq" })).toContainText(
    "12 parts read from the RFQ",
  );
  await expect(page.getByRole("region", { name: "Trace of empty_rfq" })).toContainText(
    "check step of pass 1",
  );
  await shot(page, "tests-2-traces");
});

test("e715_tests the test data opens a case to its input", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, RUNS));
  await page.getByText("Input of rfq_without_quantities").click();
  await expect(page.getByLabel("Input of rfq_without_quantities")).toContainText('"rows": 3');
  await shot(page, "tests-3-test-data-case");
});

test("e715_tests loading shows a skeleton until the runs arrive", async ({ page, baseURL }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, RUNS);
  });
  await expect(page.getByText("Loading the test runs")).toBeAttached();
  await shot(page, "tests-4-loading");
  release();
  await expect(page.getByRole("list", { name: "Test runs" })).toBeVisible();
});

test("e715_tests empty a Sub-Skill that has not been tested says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { runs: [] }));
  await expect(page.getByText("Tests not run yet")).toBeVisible();
  await shot(page, "tests-5-empty");
});

test("e715_tests error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The runs store is unreachable") : json(r, RUNS),
  );
  await expect(page.getByText("The runs store is unreachable")).toBeVisible();
  await shot(page, "tests-6-error");
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("list", { name: "Test runs" })).toBeVisible();
});

test("e715_tests forbidden an account that may not see the project gets the forbidden state", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see these tests")).toBeVisible();
  await shot(page, "tests-7-forbidden");
});
