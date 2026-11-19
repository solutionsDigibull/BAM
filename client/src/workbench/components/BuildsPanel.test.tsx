import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import type { BuildsResponse, GatesResponse } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { BuildsPanel } from "./BuildsPanel";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const BUILDS = (over: Partial<BuildsResponse> = {}): BuildsResponse => ({
  workflow_id: "w1",
  workflow_state: "BUILDING",
  active: false,
  sub_skills: [
    { sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "NEEDS_FDE_REVIEW", stage: "ast_gate", attempt: 1, gates: { ISATVON_CONTRACT: "PASSED", RUFF: "FAILED" } },
    { sub_skill_id: "s2", skill_id: "MS01.2", skill_name: "Book entry", state: "CERTIFIED", stage: "done", attempt: 0, gates: { ISATVON_CONTRACT: "PASSED" }, loop_tests: { state: "PASS", passed: 3, total: 3 } },
  ],
  workflow_gates: { UI_BUNDLE: "PASSED" },
  ...over,
});
const GATES: GatesResponse = {
  sub_skill_id: SUB,
  attempt: 1,
  gates: [
    { gate: "ISATVON_CONTRACT", status: "PASSED", findings: [] },
    { gate: "RUFF", status: "FAILED", tool_version: "ruff 0.9", findings: [{ gate: "RUFF", code: "F401", message: "unused import", file: "app/a.py", line: 3, severity: "HIGH" }] },
  ],
};

const install = (over: Partial<BamApi> = {}) =>
  setBamApi({
    getWorkflowBuilds: async () => BUILDS(),
    getSubSkillGates: async () => GATES,
    retrySubSkill: async () => ({ sub_skill_id: SUB, build_no: 4 }),
    ...over,
  } as unknown as BamApi);
const renderPanel = (id = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <BuildsPanel workflowId={id} />
      </Refine>
    </div>,
  );

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
});
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the builds of the workflow in Translate (#715)", () => {
  test("test_715_the_rows_show_state_attempt_and_the_gates_each_sub_skill_reached", async () => {
    install();
    renderPanel();
    const list = await screen.findByRole("list", { name: "Sub-Skill builds" });
    const rows = list.querySelectorAll("li.bld-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.textContent).toContain("Verify payment");
    expect(rows[0]!.textContent).toContain("Needs your review");
    expect(rows[0]!.textContent).toContain("attempt 1");
    expect(within(rows[0] as HTMLElement).getByRole("list", { name: "Gates of Verify payment" }).textContent).toContain("Ruff");
    expect(rows[1]!.textContent).toContain("Tests 3/3");
    expect(screen.getByRole("group", { name: "Build summary" }).textContent).toContain("1 with a failed gate");
    expect(screen.getByRole("list", { name: "Workflow gates" }).textContent).toContain("UI bundle");
  });

  test("test_715_a_row_opens_to_the_findings_of_one_attempts_gates", async () => {
    install();
    renderPanel();
    await screen.findByRole("list", { name: "Sub-Skill builds" });
    fireEvent.click(document.querySelector(".bld-toggle")!);
    const gates = await screen.findByRole("list", { name: "Gate results of Verify payment, attempt 1" });
    expect(gates.textContent).toContain("F401");
    expect(gates.textContent).toContain("unused import");
    expect(gates.textContent).toContain("app/a.py:3");
  });

  test("test_715_retry_is_offered_only_to_a_sub_skill_waiting_for_the_fde", async () => {
    install();
    renderPanel();
    await screen.findByRole("list", { name: "Sub-Skill builds" });
    expect(screen.getByRole("button", { name: "Retry Verify payment" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry Book entry" })).toBeNull();
  });

  test("test_715_retry_sends_the_reason_with_one_key_and_says_the_build_started", async () => {
    const retrySubSkill = vi.fn(async () => ({ sub_skill_id: SUB, build_no: 4 }));
    install({ retrySubSkill });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Retry Verify payment" }));
    const reason = await screen.findByLabelText("Reason");
    fireEvent.change(reason, { target: { value: "The fixture was wrong" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" }).at(-1)!);
    await waitFor(() => expect(retrySubSkill).toHaveBeenCalledTimes(1));
    const [wf, sub, text, key] = retrySubSkill.mock.calls[0] as unknown as string[];
    expect([wf, sub, text]).toEqual(["w1", SUB, "The fixture was wrong"]);
    expect(key).toMatch(/[0-9a-f-]{36}/);
    expect((await screen.findByText(/build 4 started/)).textContent).toContain("Verify payment");
  });

  test("test_715_a_retry_without_a_reason_is_not_sent", async () => {
    const retrySubSkill = vi.fn();
    install({ retrySubSkill });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Retry Verify payment" }));
    await screen.findByLabelText("Reason");
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" }).at(-1)!);
    expect(retrySubSkill).not.toHaveBeenCalled();
  });

  test("test_715_a_refused_retry_shows_the_servers_reason", async () => {
    install({ retrySubSkill: async () => { throw Object.assign(new Error("x"), { statusCode: 409, problem: { detail: "The Sub-Skill is not waiting for a review." } }); } });
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Retry Verify payment" }));
    fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "again" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" }).at(-1)!);
    expect((await screen.findByText("The Sub-Skill is not waiting for a review.")).textContent).toBeTruthy();
  });

  test("test_715_no_workflow_is_the_empty_state", async () => {
    install();
    renderPanel("");
    expect(await screen.findByText("No builds yet")).toBeTruthy();
  });

  test("test_715_a_workflow_with_no_sub_skills_is_the_empty_state", async () => {
    install({ getWorkflowBuilds: async () => BUILDS({ sub_skills: [] }) });
    renderPanel();
    expect(await screen.findByText("Nothing is building")).toBeTruthy();
  });

  test("test_715_a_failed_load_shows_the_detail_and_a_retry", async () => {
    install({ getWorkflowBuilds: async () => { throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "database is down" } }); } });
    renderPanel();
    expect((await screen.findByText("database is down")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("test_715_another_tenants_workflow_is_the_forbidden_state", async () => {
    install({ getWorkflowBuilds: async () => { throw Object.assign(new Error("x"), { statusCode: 404 }); } });
    renderPanel();
    expect(await screen.findByText("You can't see this workflow's builds")).toBeTruthy();
  });
});
