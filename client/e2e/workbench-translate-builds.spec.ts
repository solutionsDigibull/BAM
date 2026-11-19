import { expect, test, type Page, type Route } from "@playwright/test";

// #715: the new workbench's Translate step shows the builds of the workflow: one row per Sub-Skill with the gates it
// reached, the findings of an attempt's gates, and Retry for a Sub-Skill that waits for the FDE. The API is faked at the
// proxy (no sbam-api here). Set UI_SHOTS_DIR to save a screenshot of each state for comparison with the BAM_UI reference.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
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
      state: "NEEDS_FDE_REVIEW",
      stage: "wait_retry",
      attempt: 1,
      gates: {
        ISATVON_CONTRACT: "PASSED",
        LIBRARIES: "PASSED",
        CODEGEN: "PASSED",
        AST_SYNTAX: "PASSED",
        RUFF: "FAILED",
        BLACK: "PASSED",
        MYPY: "SKIPPED",
      },
    },
    {
      sub_skill_id: "0190f3a2-7b1c-7d4e-8f00-0000000000d2",
      skill_id: "MS01.2_Costing",
      skill_name: "Cost the parts",
      state: "CERTIFIED",
      stage: "done",
      attempt: 0,
      gates: {
        ISATVON_CONTRACT: "PASSED",
        LIBRARIES: "PASSED",
        CODEGEN: "PASSED",
        AST_SYNTAX: "PASSED",
        RUFF: "PASSED",
        LOOP_TESTS: "PASSED",
      },
      loop_tests: { state: "PASS", passed: 5, total: 5 },
    },
    {
      sub_skill_id: "0190f3a2-7b1c-7d4e-8f00-0000000000d3",
      skill_id: "MS01.3_Quote",
      skill_name: "Write the quote",
      state: "BUILDING",
      stage: "generate_bodies",
      attempt: 0,
      gates: { ISATVON_CONTRACT: "PASSED", LIBRARIES: "RUNNING" },
    },
  ],
  workflow_gates: { UI_BUNDLE: "PENDING" },
};
const GATES = {
  sub_skill_id: SUB,
  attempt: 1,
  gates: [
    { gate: "ISATVON_CONTRACT", status: "PASSED", findings: [] },
    { gate: "AST_SYNTAX", status: "PASSED", findings: [] },
    {
      gate: "RUFF",
      status: "FAILED",
      tool_version: "ruff 0.9.2",
      findings: [
        {
          gate: "RUFF",
          code: "F401",
          message: "os imported but unused",
          file: "app/intake.py",
          line: 3,
          severity: "HIGH",
        },
        {
          gate: "RUFF",
          code: "E501",
          message: "Line too long (104 > 100)",
          file: "app/intake.py",
          line: 41,
          severity: "HIGH",
        },
      ],
    },
  ],
};

async function open(page: Page, baseURL: string, builds: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/builds`, (r) => builds(r));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/gates**`,
    (r) => json(r, GATES),
  );
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Builds" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("e715_builds loaded rows show state, attempt and the gates each Sub-Skill reached; only the waiting one has Retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, BUILDS));
  const list = page.getByRole("list", { name: "Sub-Skill builds" });
  await expect(list).toContainText("Intake the RFQ");
  await expect(list).toContainText("Needs your review");
  await expect(list).toContainText("Tests 5/5");
  await expect(page.getByRole("group", { name: "Build summary" })).toContainText(
    "1 with a failed gate",
  );
  await expect(page.getByRole("button", { name: "Retry Intake the RFQ" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Retry (Cost|Write)/ })).toHaveCount(0);
  await shot(page, "builds-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "builds-1c-loaded-wide");
});

test("e715_builds gates a row opens to the findings of the gates of one attempt", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, BUILDS));
  await page.locator(".bld-toggle").first().click();
  const gates = page.getByRole("list", { name: "Gate results of Intake the RFQ, attempt 1" });
  await expect(gates).toContainText("F401");
  await expect(gates).toContainText("app/intake.py:41");
  await shot(page, "builds-2-gate-findings");
});

test("e715_builds retry asks for a reason, sends it and says the build started", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, BUILDS));
  let body: unknown;
  let key: string | undefined;
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/retry`,
    (r) => {
      body = r.request().postDataJSON();
      key = r.request().headers()["idempotency-key"];
      return json(r, { sub_skill_id: SUB, build_no: 4 }, 202);
    },
  );
  await expect(page.getByRole("list", { name: "Sub-Skill builds" })).toBeVisible();
  await page.getByRole("button", { name: "Retry Intake the RFQ" }).click();
  await expect(page.getByRole("dialog", { name: "Retry Intake the RFQ" })).toBeVisible();
  await shot(page, "builds-3-retry-dialog");
  await page.getByLabel("Reason").fill("The test fixture was wrong");
  await page
    .getByRole("dialog", { name: "Retry Intake the RFQ" })
    .getByRole("button", { name: "Retry" })
    .click();
  await expect(page.getByText("build 4 started")).toBeVisible();
  expect(body).toEqual({ reason: "The test fixture was wrong" });
  expect(key).toMatch(/^[0-9a-f-]{36}$/);
  await shot(page, "builds-4-retry-sent");
});

test("e715_builds a refused retry shows the servers reason in the dialog", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, BUILDS));
  await page.route(
    `${API}/projects/${PROJECT}/workflows/${WORKFLOW}/sub-skills/${SUB}/retry`,
    (r) => problem(r, 409, "Conflict", "The Sub-Skill is not waiting for a review."),
  );
  await expect(page.getByRole("list", { name: "Sub-Skill builds" })).toBeVisible();
  await page.getByRole("button", { name: "Retry Intake the RFQ" }).click();
  await page.getByLabel("Reason").fill("again");
  await page
    .getByRole("dialog", { name: "Retry Intake the RFQ" })
    .getByRole("button", { name: "Retry" })
    .click();
  await expect(
    page
      .getByRole("dialog", { name: "Retry Intake the RFQ" })
      .getByText("The Sub-Skill is not waiting for a review."),
  ).toBeVisible();
  await shot(page, "builds-5-retry-refused");
});

test("e715_builds loading shows a skeleton until the builds arrive", async ({ page, baseURL }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, BUILDS);
  });
  await expect(page.getByText("Loading the builds")).toBeAttached();
  await shot(page, "builds-6-loading");
  release();
  await expect(page.getByRole("list", { name: "Sub-Skill builds" })).toBeVisible();
});

test("e715_builds empty a workflow with no Sub-Skills says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { ...BUILDS, sub_skills: [] }));
  await expect(page.getByText("Nothing is building")).toBeVisible();
  await shot(page, "builds-7-empty");
});

test("e715_builds error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The database is unreachable") : json(r, BUILDS),
  );
  await expect(page.getByText("The database is unreachable")).toBeVisible();
  await shot(page, "builds-8-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("list", { name: "Sub-Skill builds" })).toBeVisible();
});

test("e715_builds forbidden an account that may not see the project gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this workflow's builds")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "builds-9-forbidden");
});
