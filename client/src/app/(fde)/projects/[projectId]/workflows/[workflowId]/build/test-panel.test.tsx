import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildRow, HarnessTrace, TestCaseOut, TestDatasetOut, TestRunOut } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import {
  canDelete,
  caseResult,
  FROZEN_MESSAGE,
  inputProblems,
  isReadOnly,
  jsonText,
  MIN_CASES_MESSAGE,
  NO_TRACE_MESSAGE,
  NOT_RUN_MESSAGE,
  STALE_MESSAGE,
} from "./test-panel-api";
import { TestPanel } from "./test-panel";

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const S = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const RUN = "0190f3a2-7b1c-7d4e-8f00-0000000000e1";
const BASE = `/projects/${P}/workflows/${W}/sub-skills/${S}`;
const SHA = "a".repeat(64);
const XSS = "<img src=x onerror=alert(1)>";

const SCHEMA = {
  type: "object",
  properties: { otp: { type: "string", pattern: "^[0-9]{6}$" }, attempts: { type: "integer" } },
  required: ["otp"],
  additionalProperties: false,
};

const testCase = (name: string, over: Partial<TestCaseOut> = {}): TestCaseOut => ({
  id: `id-${name.replace(/\W+/g, "-")}`,
  name,
  kind: "PASS_FIRST",
  input: { otp: "123456" },
  initial_state: [],
  state_script: [],
  expected_terminal: "SUCCESS",
  assertions: [],
  ...over,
});

const dataset = (cases: TestCaseOut[]): TestDatasetOut => ({
  version: 2,
  sha256: SHA,
  synthetic: true,
  edited_by_fde: false,
  files_keys: [],
  mocks_keys: [],
  created_at: "2026-10-30T10:00:00Z",
  cases,
});

const THREE = [
  testCase("first try"),
  testCase("locked out", { kind: "ESCALATION", expected_terminal: "ESCALATED" }),
  testCase("nothing to do", { kind: "NO_OP", expected_terminal: "NO_OP" }),
];

const run = (over: Partial<TestRunOut> = {}): TestRunOut => ({
  id: RUN,
  sub_skill_id: S,
  code_attempt_id: S,
  sandbox_run_id: S,
  suite_sha256: SHA,
  state: "FAIL",
  total: 3,
  passed: 2,
  failed: 1,
  non_certifying: false,
  results: [
    { case: "first try", type: "terminal_state", passed: true, message: "" },
    { case: "locked out", type: "terminal_state", passed: false, message: "ended SUCCESS" },
  ],
  created_at: "2026-10-30T10:00:00Z",
  ...over,
});

const trace = (over: Partial<HarnessTrace> = {}): HarnessTrace => ({
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
    action_ms: 10 * n,
    check_ms: 5,
    ui_update: `Looping [Pass ${n}/3]`,
    gate_eval: { success: false, escalation: n === 3 },
  })),
  ...over,
});

const row = (over: Partial<BuildRow> = {}): BuildRow => ({
  sub_skill_id: S,
  skill_id: "MS01.1_Investor_Auth",
  skill_name: "OTP verifier",
  state: "NEEDS_FDE_REVIEW",
  stage: "run_sandbox_tests",
  attempt: 0,
  gates: {},
  ...over,
});

function api(
  cases: TestCaseOut[] = THREE,
  runs: TestRunOut[] = [run()],
  extra: Parameters<typeof fakeApi>[0] = {},
) {
  return fakeApi({
    [`GET ${BASE}/tests`]: {
      body: { dataset: dataset(cases), input_schema: SCHEMA },
      etag: `"${SHA}"`,
    },
    [`GET ${BASE}/test-runs`]: { body: { runs } },
    [`GET ${BASE}/test-runs/${RUN}/traces`]: { body: { run_id: RUN, traces: [trace()] } },
    ...extra,
  });
}

const open = (over: Partial<BuildRow> = {}, onRetry = vi.fn()) =>
  renderWithRefine(
    <TestPanel projectId={P} workflowId={W} row={row(over)} onClose={vi.fn()} onRetry={onRetry} />,
  );

afterEach(() => vi.unstubAllGlobals());

describe("test panel rules (E13.7)", () => {
  test("delete is allowed only above three cases", () => {
    expect([2, 3, 4, 20].map(canDelete)).toEqual([false, false, true, true]);
  });

  test("certified, frozen and superseded Sub-Skills are read-only", () => {
    expect(["CERTIFIED", "FROZEN", "SUPERSEDED"].every((s) => isReadOnly(s as never))).toBe(true);
    expect(isReadOnly("NEEDS_FDE_REVIEW")).toBe(false);
  });

  test("a case passes only when all its assertions passed", () => {
    expect(caseResult(run(), "first try")).toBe("PASSED");
    expect(caseResult(run(), "locked out")).toBe("FAILED");
    expect(caseResult(run(), "nothing to do")).toBe("NOT_RUN");
    expect(caseResult(undefined, "first try")).toBe("NOT_RUN");
  });

  test("E13.7-T05 invalid input gives one error per path, as the server's 422 names them", () => {
    expect(inputProblems('{"otp": "123456"}', SCHEMA)).toEqual([]);
    const problems = inputProblems('{"otp": 12, "attempts": "x", "typo": 1}', SCHEMA);
    expect(problems.map((p) => p.path).sort()).toEqual([
      "/input/attempts",
      "/input/otp",
      "/input/typo",
    ]);
    expect(inputProblems("{}", SCHEMA)).toEqual([{ path: "/input/otp", message: "is required" }]);
    expect(inputProblems("{oops", SCHEMA)[0]!.path).toBe("/input");
    expect(inputProblems("[1]", SCHEMA)[0]!.message).toBe("The input must be a JSON object");
    expect(inputProblems('{"anything": 1}', null)).toEqual([]); // no schema: the server decides
  });

  test("JSON over 2 KB is marked for collapsing; strings stay as they are", () => {
    expect(jsonText({ a: 1 }).long).toBe(false);
    expect(jsonText({ a: "x".repeat(3000) }).long).toBe(true);
    expect(jsonText(XSS).text).toBe(XSS);
  });
});

describe("test panel (E13.7)", () => {
  test("lists the cases with kind, expected terminal and the last result", async () => {
    api();
    open();
    expect(await screen.findByText("first try")).toBeDefined();
    expect(screen.getByText("ESCALATION")).toBeDefined();
    expect(screen.getByText("Passed")).toBeDefined();
    expect(screen.getByText("Failed")).toBeDefined();
    expect(screen.getByText("Not run")).toBeDefined();
    expect(screen.getByText("2/3 cases passed")).toBeDefined();
  });

  test("E13.7-T02 with exactly three cases every delete is disabled with 'Minimum 3 cases'", async () => {
    api();
    open();
    await screen.findByText("first try");
    for (const c of THREE) {
      const button = screen.getByRole("button", { name: `Delete ${c.name}` });
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(button.parentElement!.getAttribute("title")).toBe(MIN_CASES_MESSAGE);
    }
  });

  test("with four cases a delete sends If-Match and the list reloads", async () => {
    const four = [...THREE, testCase("mine", { kind: "FDE" })];
    const { calls } = api(four, [run()], {
      [`DELETE ${BASE}/tests/cases/id-mine`]: { status: 204 },
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Delete mine" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const del = calls.find((c) => c.method === "DELETE")!;
    expect(new Headers(del.init.headers).get("if-match")).toBe(`"${SHA}"`);
  });

  test("a server 409 MIN_TEST_CASES is shown and the case comes back", async () => {
    const four = [...THREE, testCase("mine", { kind: "FDE" })];
    api(four, [run()], {
      [`DELETE ${BASE}/tests/cases/id-mine`]: problem(409, "MIN_TEST_CASES", "too few"),
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Delete mine" }));
    expect(await screen.findByText(/Minimum 3 cases: a Sub-Skill keeps/)).toBeDefined();
    expect(screen.getByText("mine")).toBeDefined(); // the optimistic removal is rolled back
  });

  test("E13.7-T05 an input that breaks the schema shows errors per path and disables Save", async () => {
    api();
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Edit first try" }));
    const editor = await screen.findByLabelText("Input JSON");
    fireEvent.change(editor, { target: { value: '{"otp": 12}' } });
    const errors = await screen.findByRole("list", { name: "Input errors" });
    expect(within(errors).getByText("/input/otp")).toBeDefined();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(editor, { target: { value: '{"otp": "654321"}' } });
    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
  });

  test("E13.7-T04 adding a case posts name, input and terminal with If-Match", async () => {
    const { calls } = api(THREE, [run()], {
      [`POST ${BASE}/tests/cases`]: { status: 201, body: testCase("mine", { kind: "FDE" }) },
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Add case" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "mine" } });
    fireEvent.change(screen.getByLabelText("Input JSON"), {
      target: { value: '{"otp": "111111"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(JSON.parse(String(post.init.body))).toEqual({
      name: "mine",
      input: { otp: "111111" },
      expected_terminal: "SUCCESS",
    });
    expect(new Headers(post.init.headers).get("if-match")).toBe(`"${SHA}"`);
  });

  test("E13.7-T06 a stale ETag (412) shows the reload banner and overwrites nothing", async () => {
    api(THREE, [run()], {
      [`PUT ${BASE}/tests/cases/id-first-try`]: problem(412, "PRECONDITION_FAILED"),
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Edit first try" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByText(STALE_MESSAGE)).toBeDefined();
    expect(screen.getByRole("button", { name: "Reload" })).toBeDefined();
  });

  test("E13.7-T08 a frozen Sub-Skill is read-only: no add, edit or delete", async () => {
    api();
    open({ state: "FROZEN" });
    await screen.findByText("first try");
    expect(screen.getByText(FROZEN_MESSAGE)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Add case" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete / })).toBeNull();
  });

  test("a 423 from the server reads 'Frozen — create a new version'", async () => {
    api(THREE, [run()], {
      [`PUT ${BASE}/tests/cases/id-first-try`]: problem(423, "RESOURCE_LOCKED"),
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Edit first try" }));
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByText(FROZEN_MESSAGE)).toBeDefined();
  });

  test("no runs yet shows 'Tests not run yet'", async () => {
    api(THREE, []);
    open();
    expect(await screen.findByText(NOT_RUN_MESSAGE)).toBeDefined();
  });

  test("a local-docker run is badged NON-CERTIFYING, never as a pass", async () => {
    api(THREE, [run({ state: "NON_CERTIFYING", passed: 3, failed: 0, non_certifying: true })]);
    open();
    expect(await screen.findByText("NON-CERTIFYING (local-docker)")).toBeDefined();
    expect(screen.queryByText("PASS")).toBeNull();
  });

  test("TESTING_BLOCKED shows a banner whose Retry starts the retry", async () => {
    api();
    const onRetry = vi.fn();
    open({ state: "TESTING_BLOCKED" }, onRetry);
    await screen.findByText(/Testing is blocked/);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ sub_skill_id: S }));
  });

  test("E13.7-T01 the trace shows each pass's ui_update and one handoff row per pass", async () => {
    api();
    open();
    fireEvent.click(await screen.findByRole("tab", { name: "Trace" }));
    fireEvent.click(await screen.findByRole("button", { name: /locked out/ }));
    expect(await screen.findAllByText("Looping [Pass 1/3]")).not.toHaveLength(0);
    expect(screen.getAllByText("Looping [Pass 3/3]")).not.toHaveLength(0);
    expect(screen.getByText("3 passes · 3 handoff rows")).toBeDefined();
    const table = screen.getByLabelText("Handoff rows");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 3); // header + one per pass
  });

  test("E13.7-T07 markup in a tool output is shown literally and never runs", async () => {
    api(THREE, [run()], {
      [`GET ${BASE}/test-runs/${RUN}/traces`]: {
        body: {
          run_id: RUN,
          traces: [
            trace({
              passes: [{ ...trace().passes[0]!, action_output: { note: XSS }, ui_update: XSS }],
            }),
          ],
        },
      },
    });
    const { container } = open();
    fireEvent.click(await screen.findByRole("tab", { name: "Trace" }));
    fireEvent.click(await screen.findByRole("button", { name: /locked out/ }));
    await screen.findByLabelText("Action output");
    expect(container.ownerDocument.querySelector("img")).toBeNull();
    expect(screen.getByLabelText("Action output").textContent).toContain(XSS);
  });

  test("a run without recorded traces says so", async () => {
    api(THREE, [run()], {
      [`GET ${BASE}/test-runs/${RUN}/traces`]: { body: { run_id: RUN, traces: [] } },
    });
    open();
    fireEvent.click(await screen.findByRole("tab", { name: "Trace" }));
    expect(await screen.findByText(NO_TRACE_MESSAGE)).toBeDefined();
  });

  test("another tenant's or an unassigned Sub-Skill is not found", async () => {
    fakeApi({
      [`GET ${BASE}/tests`]: problem(404, "NOT_FOUND"),
      [`GET ${BASE}/test-runs`]: problem(404, "NOT_FOUND"),
    });
    open();
    expect(await screen.findByText("Not found")).toBeDefined();
  });
});
