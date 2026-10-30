import { expect, test, type Page, type Route } from "@playwright/test";

// E13.7 test panel, opened from the Build & Test grid. The tests, test-runs and traces endpoints
// are faked at the proxy with the shapes of sbam_contracts.testgen / promptfoo / harness; traces
// are recorded for real once the Promptfoo runner is wired (#526).

const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const WORKFLOW = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const RUN = "0190f3a2-7b1c-7d4e-8f00-0000000000e1";
const PAGE = `/projects/${PROJECT}/workflows/${WORKFLOW}/build`;
const SKILL = "MS01.1_Investor_Auth";
const SHA = "a".repeat(64);
const SHA2 = "b".repeat(64);

const json = (route: Route, body: unknown, status = 200, etag?: string) =>
  route.fulfill({
    status,
    contentType: status >= 400 ? "application/problem+json" : "application/json",
    headers: etag ? { etag: `"${etag}"` } : {},
    body: JSON.stringify(body),
  });
const problem = (route: Route, status: number, code: string) =>
  json(route, { type: "about:blank", title: code, status, detail: code, code }, status);

const testCase = (n: number, name: string, kind: string, terminal: string) => ({
  id: `0190f3a2-7b1c-7d4e-8f00-${String(n).padStart(12, "0")}`,
  name,
  kind,
  input: { otp: "123456" },
  initial_state: [],
  state_script: [],
  expected_terminal: terminal,
  assertions: [],
});
const THREE = [
  testCase(1, "first try", "PASS_FIRST", "SUCCESS"),
  testCase(2, "locked out", "ESCALATION", "ESCALATED"),
  testCase(3, "nothing to do", "NO_OP", "NO_OP"),
];
const dataset = (version: number, sha: string, cases: unknown[]) => ({
  dataset: {
    version,
    sha256: sha,
    synthetic: true,
    edited_by_fde: version > 1,
    files_keys: [],
    mocks_keys: [],
    created_at: "2026-10-30T10:00:00Z",
    cases,
  },
  input_schema: {
    type: "object",
    properties: { otp: { type: "string", pattern: "^[0-9]{6}$" } },
    required: ["otp"],
    additionalProperties: false,
  },
});
const RUNS = {
  runs: [
    {
      id: RUN,
      sub_skill_id: SUB,
      code_attempt_id: SUB,
      sandbox_run_id: SUB,
      suite_sha256: SHA,
      state: "PASS",
      total: 3,
      passed: 3,
      failed: 0,
      non_certifying: false,
      results: [],
      created_at: "2026-10-30T10:00:00Z",
    },
  ],
};
const TRACES = {
  run_id: RUN,
  traces: [
    {
      case_id: "locked out",
      terminal: "ESCALATED",
      iterations: 3,
      handoff_rows: 3,
      ui_updates: [],
      passes: [1, 2, 3].map((n) => ({
        pass: n,
        fresh_state: { attempts: n },
        action_output: { ok: false },
        check_output: { verdict: "retry" },
        action_ms: 12,
        check_ms: 4,
        ui_update: `Looping [Pass ${n}/3]`,
        gate_eval: { success: false, escalation: n === 3 },
      })),
    },
  ],
};

type Api = { tests: () => [unknown, string]; write?: (route: Route, method: string) => unknown };

async function open(page: Page, baseURL: string, api: Api, state = "NEEDS_FDE_REVIEW") {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route("**/api/proxy/api/v1/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/proxy/api/v1", "");
    if (path.endsWith("/builds"))
      return json(route, {
        workflow_id: WORKFLOW,
        workflow_state: "BUILDING",
        active: false,
        sub_skills: [
          {
            sub_skill_id: SUB,
            skill_id: SKILL,
            skill_name: "OTP verifier",
            state,
            stage: "run_sandbox_tests",
            attempt: 0,
            gates: { LOOP_TESTS: "PASSED" },
          },
        ],
      });
    if (path.endsWith("/tests") && req.method() === "GET") {
      const [body, etag] = api.tests();
      return json(route, body, 200, etag);
    }
    if (path.includes("/tests/cases") && api.write) return api.write(route, req.method());
    if (path.endsWith("/test-runs")) return json(route, RUNS);
    if (path.endsWith("/traces")) return json(route, TRACES);
    return problem(route, 404, "NOT_FOUND");
  });
  await page.goto(PAGE);
  await page.getByRole("button", { name: `Tests ${SKILL}` }).click();
  return page.getByRole("dialog", { name: new RegExp(`${SKILL} · Loop tests`) });
}

test("e13_7_t01 the trace shows Looping [Pass x/3] per pass and a handoff row per pass", async ({
  page,
  baseURL,
}) => {
  const panel = await open(page, baseURL!, { tests: () => [dataset(1, SHA, THREE), SHA] });
  await panel.getByRole("tab", { name: "Trace" }).click();
  await panel.getByRole("button", { name: /locked out/ }).click();
  for (const n of [1, 2, 3])
    await expect(panel.getByRole("cell", { name: `Looping [Pass ${n}/3]` })).toBeVisible();
  await expect(panel.getByLabel("Handoff rows").getByRole("row")).toHaveCount(1 + 3);
});

test("e13_7_t04 an added FDE case appears as kind FDE and the dataset version increments", async ({
  page,
  baseURL,
}) => {
  let added = false;
  const panel = await open(page, baseURL!, {
    tests: () =>
      added
        ? [dataset(2, SHA2, [...THREE, testCase(4, "mine", "FDE", "SUCCESS")]), SHA2]
        : [dataset(1, SHA, THREE), SHA],
    write: (route, method) => {
      if (method !== "POST") return problem(route, 405, "METHOD_NOT_ALLOWED");
      added = true;
      return json(route, testCase(4, "mine", "FDE", "SUCCESS"), 201, SHA2);
    },
  });
  await expect(panel.getByText("Dataset v1")).toBeVisible();
  await panel.getByRole("button", { name: "Add case" }).click();
  const editor = page.getByRole("dialog", { name: "Add a test case" });
  await editor.getByLabel("Name").fill("mine");
  await editor.getByLabel("Input JSON").fill('{"otp": "111111"}');
  await editor.getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("row", { name: /mine\s+FDE/ })).toBeVisible();
  await expect(panel.getByText("Dataset v2")).toBeVisible();
});

test("e13_7_t06 a stale ETag shows the reload banner and nothing is overwritten", async ({
  page,
  baseURL,
}) => {
  const panel = await open(page, baseURL!, {
    tests: () => [dataset(1, SHA, THREE), SHA],
    write: (route) => problem(route, 412, "PRECONDITION_FAILED"),
  });
  await panel.getByRole("button", { name: "Edit first try" }).click();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Someone else changed these test cases/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
});

test("e13_7_t08 a frozen Sub-Skill is read-only", async ({ page, baseURL }) => {
  const panel = await open(
    page,
    baseURL!,
    { tests: () => [dataset(1, SHA, THREE), SHA] },
    "FROZEN",
  );
  await expect(panel.getByText("Frozen — create a new version")).toBeVisible();
  await expect(panel.getByRole("cell", { name: "first try" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Add case" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: /^(Edit|Delete) / })).toHaveCount(0);
});
