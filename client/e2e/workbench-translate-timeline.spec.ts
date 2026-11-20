import { expect, test, type Page, type Route } from "@playwright/test";

// #926: the new workbench's Translate step has a Timeline view: each Sub-Skill's newest build as ordered steps with tokens
// and cost, and why a parked build stopped. The API is faked at the
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
const step = (seq: number, kind: string, name: string, over: Record<string, unknown> = {}) => ({
  seq,
  kind,
  name,
  status: "OK",
  started_at: "2026-11-20T10:00:00Z",
  duration_ms: 60000,
  calls: 0,
  tokens_in: 0,
  tokens_out: 0,
  cost_usd: "0.0000",
  findings: [],
  ...over,
});
const TIMELINE = {
  workflow_id: WORKFLOW,
  workflow_state: "BUILDING",
  cost_usd: "0.0540",
  sub_skills: [
    {
      sub_skill_id: SUB,
      skill_id: "MS01.1_Intake",
      skill_name: "Intake the RFQ",
      state: "NEEDS_FDE_REVIEW",
      build_id: `build-${SUB}-1`,
      build_no: 1,
      started_at: "2026-11-20T10:00:00Z",
      last_activity_at: "2026-11-20T10:09:00Z",
      current_stage: "assemble",
      code_attempts: 2,
      llm_calls: 3,
      tokens_in: 3200,
      tokens_out: 410,
      cost_usd: "0.0540",
      failure: {
        event: "generation_failed",
        state: "NEEDS_FDE_REVIEW",
        at: "2026-11-20T10:09:00Z",
        failing_gates: ["AST_IMPORTS"],
        findings: [
          { gate: "CODEGEN", code: "STATE_QUERY_INVALID", file: "app/intake.py", line: 12 },
          { gate: "AST_IMPORTS", code: "IMPORT_NOT_ALLOWED", file: "app/intake.py", line: 1 },
        ],
      },
      steps: [
        step(1, "TRANSITION", "start_build", { from_state: "VALID", to_state: "BUILDING" }),
        step(2, "MODEL", "generate_bodies", {
          alias: "sbam-codegen",
          calls: 2,
          tokens_in: 2400,
          tokens_out: 300,
          cost_usd: "0.0400",
          duration_ms: 90000,
        }),
        step(3, "CODE_ATTEMPT", "PRECHECK_FAILED", {
          status: "FAILED",
          cycle: 0,
          attempt: 0,
          findings: [
            { gate: "CODEGEN", code: "STATE_QUERY_INVALID", file: "app/intake.py", line: 12 },
          ],
        }),
        step(4, "GATE", "AST_IMPORTS", {
          status: "FAILED",
          cycle: 0,
          attempt: 0,
          findings: [
            { gate: "AST_IMPORTS", code: "IMPORT_NOT_ALLOWED", file: "app/intake.py", line: 1 },
          ],
        }),
        step(5, "TRANSITION", "generation_failed", {
          status: "FAILED",
          from_state: "BUILDING",
          to_state: "NEEDS_FDE_REVIEW",
          duration_ms: null,
        }),
      ],
    },
  ],
};

async function open(page: Page, baseURL: string, timeline: (route: Route) => unknown) {
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
  await page.route(`${API}/projects/${PROJECT}/workflows/${WORKFLOW}/timeline`, (r) => timeline(r));
  await page.goto(`/projects/${PROJECT}/workbench#/workbench/code`);
  await page.getByRole("button", { name: "Timeline" }).click();
}

const shot = async (page: Page, name: string) => {
  const dir = process.env.UI_SHOTS_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
};

test("wg39_timeline loaded a parked build shows why it stopped and its steps with tokens and cost", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => json(r, TIMELINE));
  const why = page.getByRole("note", { name: "Why Intake the RFQ stopped" });
  await expect(why).toContainText("generation failed");
  await expect(why).toContainText("IMPORT_NOT_ALLOWED");
  const steps = page.getByRole("list", { name: "Steps of Intake the RFQ" });
  await expect(steps).toContainText("generate bodies (sbam-codegen, 2 calls)");
  await expect(steps).toContainText("$0.0400");
  await expect(page.getByLabel("Build totals")).toContainText("$0.0540");
  await shot(page, "timeline-1-loaded");
  await page.setViewportSize({ width: 1920, height: 1000 });
  await shot(page, "timeline-1b-loaded-wide");
});

test("wg39_timeline loading shows a skeleton until the timeline arrives", async ({
  page,
  baseURL,
}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((res) => (release = res));
  await open(page, baseURL!, async (r) => {
    await gate;
    return json(r, TIMELINE);
  });
  await expect(page.getByText("Loading the timeline")).toBeAttached();
  await shot(page, "timeline-2-loading");
  release();
  await expect(page.getByRole("list", { name: "Sub-Skill timelines" })).toBeVisible();
});

test("wg39_timeline empty a workflow with no Sub-Skills says so", async ({ page, baseURL }) => {
  await open(page, baseURL!, (r) => json(r, { ...TIMELINE, sub_skills: [] }));
  await expect(page.getByText("Nothing has been built")).toBeVisible();
  await shot(page, "timeline-3-empty");
});

test("wg39_timeline error a server error shows its reason and Retry asks again", async ({
  page,
  baseURL,
}) => {
  let failing = true;
  await open(page, baseURL!, (r) =>
    failing ? problem(r, 500, "Server error", "The database is unreachable") : json(r, TIMELINE),
  );
  await expect(page.getByText("The database is unreachable")).toBeVisible();
  await shot(page, "timeline-4-error");
  failing = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("list", { name: "Sub-Skill timelines" })).toBeVisible();
});

test("wg39_timeline forbidden an account that may not see the project gets the forbidden state, no retry", async ({
  page,
  baseURL,
}) => {
  await open(page, baseURL!, (r) => problem(r, 403, "Forbidden"));
  await expect(page.getByText("You can't see this workflow's timeline")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await shot(page, "timeline-5-forbidden");
});
