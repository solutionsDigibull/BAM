import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { BuildsResponse, TestRunOut, TestRunTraces, TestRunsResponse, TestsResponse } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { TestsPanel } from "./TestsPanel";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const RUN_ID = "0190f3a2-7b1c-7d4e-8f00-0000000000e1";
const SANDBOX = "0190f3a2-7b1c-7d4e-8f00-0000000000f1";
const BUILDS: BuildsResponse = {
  workflow_id: "w1", workflow_state: "BUILDING", active: false,
  sub_skills: [{ sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "TESTING", stage: "run_sandbox_tests", attempt: 0, gates: {} }],
};
const TESTS: TestsResponse = {
  dataset: {
    version: 2, sha256: "a".repeat(64), edited_by_fde: false, files_keys: [], created_at: "2026-11-18T10:00:00Z",
    cases: [
      { id: "c1", name: "happy_path", input: { amount: 10 }, expected_terminal: "SUCCESS", expected_output: { ok: true }, kind: "PASS_FIRST" },
      { id: "c2", name: "over_limit", input: { amount: 99999 }, expected_terminal: "ESCALATED", kind: "ESCALATION" },
    ],
  },
};
const RUN = (over: Partial<TestRunOut> = {}): TestRunOut => ({
  id: RUN_ID, sub_skill_id: SUB, code_attempt_id: "c1", sandbox_run_id: SANDBOX, suite_sha256: "b".repeat(64), state: "FAIL", total: 3, passed: 2, failed: 1,
  results: [
    { case: "happy_path", type: "terminal_state", passed: true },
    { case: "happy_path", type: "output_schema", passed: true },
    { case: "over_limit", type: "terminal_state", passed: false, message: "ended SUCCESS" },
  ],
  created_at: "2026-11-18T11:00:00Z", ...over,
});
const TRACES: TestRunTraces = {
  run_id: RUN_ID,
  traces: [
    { case_id: "happy_path", terminal: "SUCCESS", iterations: 1, handoff_rows: 1, ui_updates: ["done"], passes: [{ pass: 1, fresh_state: {}, action_output: {}, check_output: {}, action_ms: 12, check_ms: 3, ui_update: "Payment verified", gate_eval: { success: true, escalation: false } }] },
    { case_id: "over_limit", terminal: null, iterations: 2, handoff_rows: 0, ui_updates: [], passes: [], error: { category: "ASSERTION_FAILED", pass: 2, step: "check", elapsed_ms: 5, message: "ended SUCCESS, expected ESCALATED" } },
  ],
};

const install = (over: Partial<BamApi> = {}) =>
  setBamApi({
    getWorkflowBuilds: async () => BUILDS,
    getSubSkillTests: async () => TESTS,
    getTestRuns: async (): Promise<TestRunsResponse> => ({ runs: [RUN()] }),
    getTestRunTraces: async () => TRACES,
    ...over,
  } as unknown as BamApi);
const renderPanel = (id = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <TestsPanel workflowId={id} />
      </Refine>
    </div>,
  );
beforeEach(() => window.history.pushState({}, "", "/projects/p1/workbench"));
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the tests of a Sub-Skill in Translate (#715)", () => {
  test("test_715_a_run_shows_its_state_the_pass_count_and_each_cases_result", async () => {
    install();
    renderPanel();
    const runs = await screen.findByRole("list", { name: "Test runs" });
    expect(runs.textContent).toContain("Failed");
    expect(runs.textContent).toContain("2 of 3 passed");
    const results = within(runs).getByRole("list", { name: "Results by case" });
    expect(results.textContent).toContain("happy_path");
    expect(results.textContent).toContain("terminal state failed (ended SUCCESS)");
  });

  test("test_715_the_sandbox_log_is_a_download_of_that_runs_route", async () => {
    install();
    renderPanel();
    const link = (await screen.findByText("Sandbox log")).closest("a")!;
    expect(link.getAttribute("href")).toBe(`/api/proxy/api/v1/projects/p1/workflows/w1/sandbox-runs/${SANDBOX}/logs`);
    expect(link.hasAttribute("download")).toBe(true);
  });

  test("test_715_the_traces_load_on_request_and_show_each_pass_and_the_failing_step", async () => {
    const getTestRunTraces = vi.fn(async () => TRACES);
    install({ getTestRunTraces });
    renderPanel();
    await screen.findByRole("list", { name: "Test runs" });
    expect(getTestRunTraces).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Show the traces" }));
    const trace = await screen.findByRole("region", { name: "Trace of happy_path" });
    expect(trace.textContent).toContain("action 12 ms");
    expect(trace.textContent).toContain("Payment verified");
    const failing = screen.getByRole("region", { name: "Trace of over_limit" });
    expect(failing.textContent).toContain("assertion failed");
    expect(failing.textContent).toContain("check step of pass 2");
    expect(getTestRunTraces).toHaveBeenCalledWith("w1", SUB, RUN_ID);
  });

  test("test_715_a_non_certifying_run_says_it_does_not_certify", async () => {
    install({ getTestRuns: async () => ({ runs: [RUN({ state: "NON_CERTIFYING", non_certifying: true })] }) });
    renderPanel();
    expect((await screen.findByText(/This run does not certify/)).textContent).toBeTruthy();
    expect(screen.getByText("Not certifying")).toBeTruthy();
  });

  test("test_715_the_test_data_lists_the_cases_with_their_input_as_text", async () => {
    install();
    renderPanel();
    const cases = await screen.findByRole("list", { name: "Test cases" });
    expect(cases.textContent).toContain("Passes first time");
    expect(cases.textContent).toContain("Escalates");
    expect(within(cases).getByLabelText("Input of over_limit").textContent).toContain("99999");
    expect(screen.getByText("Synthetic, generated")).toBeTruthy();
  });

  test("test_715_the_panel_has_no_way_to_edit_a_case_or_start_a_run", async () => {
    install();
    renderPanel();
    await screen.findByRole("list", { name: "Test cases" });
    expect(screen.queryByRole("button", { name: /add|edit|delete|run|start/i })).toBeNull();
    expect(document.querySelector("textarea, input")).toBeNull();
  });

  test("test_715_no_runs_and_no_test_data_are_empty_states", async () => {
    install({ getTestRuns: async () => ({ runs: [] }), getSubSkillTests: async () => ({ dataset: null }) });
    renderPanel();
    expect(await screen.findByText("Tests not run yet")).toBeTruthy();
    expect(await screen.findByText("No test data yet")).toBeTruthy();
  });

  test("test_715_another_sub_skill_reads_its_own_runs", async () => {
    const getTestRuns = vi.fn(async () => ({ runs: [] }));
    install({
      getWorkflowBuilds: async () => ({ ...BUILDS, sub_skills: [...BUILDS.sub_skills, { ...BUILDS.sub_skills[0]!, sub_skill_id: "s2", skill_name: "Book entry" }] }),
      getTestRuns,
    });
    renderPanel();
    await screen.findByText("Tests not run yet");
    fireEvent.change(screen.getByLabelText("Sub-Skill"), { target: { value: "s2" } });
    await waitFor(() => expect(getTestRuns).toHaveBeenCalledWith("w1", "s2"));
  });

  test("test_715_a_failed_load_shows_the_detail_and_a_retry", async () => {
    install({ getTestRuns: async () => { throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "the runs store is down" } }); } });
    renderPanel();
    expect((await screen.findByText("the runs store is down")).textContent).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(0);
  });

  test("test_715_another_tenants_workflow_is_the_forbidden_state", async () => {
    install({ getWorkflowBuilds: async () => { throw Object.assign(new Error("x"), { statusCode: 404 }); } });
    renderPanel();
    expect(await screen.findByText("You can't see these tests")).toBeTruthy();
  });

  test("test_715_no_workflow_is_the_empty_state", async () => {
    install();
    renderPanel("");
    expect(await screen.findByText("No tests yet")).toBeTruthy();
  });
});
