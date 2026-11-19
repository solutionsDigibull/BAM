import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildsResponse, CodeAttemptList, CodeDiff, CodeFileView } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { AttemptsPanel } from "./AttemptsPanel";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const BUILDS: BuildsResponse = {
  workflow_id: "w1", workflow_state: "BUILDING", active: false,
  sub_skills: [
    { sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "NEEDS_FDE_REVIEW", stage: "wait_retry", attempt: 1, gates: {} },
    { sub_skill_id: "s2", skill_id: "MS01.2", skill_name: "Book entry", state: "CERTIFIED", stage: "done", attempt: 0, gates: {} },
  ],
};
const sha = "a".repeat(64);
const ATTEMPTS: CodeAttemptList = {
  items: [
    { build_no: 1, cycle: 0, attempt: 0, status: "FAILED", template_name: "skill", template_version: "1.2.0", files: [{ path: "app/skill.py", sha256: sha }], findings: [{ gate: "RUFF", code: "F401", message: "unused import", file: "app/skill.py", line: 2 }] },
    { build_no: 1, cycle: 0, attempt: 1, status: "PASSED", template_name: "skill", template_version: "1.2.0", correction_reason: "Removed the unused import", files: [{ path: "app/skill.py", sha256: sha }, { path: "app/util.py", sha256: sha }], findings: [] },
  ],
};
const FILE: CodeFileView = {
  path: "app/skill.py", sha256: sha, content: "import os\nimport json\n# >>> LLM_BODY:run\nreturn 1\n# <<< LLM_BODY:run\n", truncated: false,
  slots: [{ start: 4, end: 4 }], findings: [{ gate: "RUFF", code: "F401", message: "unused import", file: "app/skill.py", line: 1 }],
};
const DIFF: CodeDiff = { from_attempt: 0, to_attempt: 1, path: "app/skill.py", changed: true, unified: "--- a/app/skill.py\n+++ b/app/skill.py\n@@ -1,2 +1,1 @@\n-import os\n+import json", truncated: false };

const install = (over: Partial<BamApi> = {}) =>
  setBamApi({
    getWorkflowBuilds: async () => BUILDS,
    getCodeAttempts: async () => ATTEMPTS,
    getCodeAttemptFile: async () => FILE,
    getCodeAttemptDiff: async () => DIFF,
    ...over,
  } as unknown as BamApi);
const renderPanel = (id = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <AttemptsPanel workflowId={id} />
      </Refine>
    </div>,
  );
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the code attempts of a Sub-Skill in Translate (#715)", () => {
  test("test_715_the_attempts_list_newest_first_with_status_template_reason_files_and_findings", async () => {
    install();
    renderPanel();
    const list = await screen.findByRole("list", { name: "Attempts" });
    const cards = list.querySelectorAll("li");
    expect(cards).toHaveLength(2);
    expect(cards[0]!.textContent).toContain("Correction 1");
    expect(cards[0]!.textContent).toContain("Removed the unused import");
    expect(cards[0]!.textContent).toContain("2 files");
    expect(cards[1]!.textContent).toContain("First generation");
    expect(cards[1]!.textContent).toContain("1 finding");
  });

  test("test_715_a_file_marks_the_generated_body_and_puts_the_finding_on_its_line", async () => {
    install();
    renderPanel();
    const code = await screen.findByRole("list", { name: "Content of app/skill.py" });
    const lines = code.querySelectorAll("li");
    expect(lines[0]!.className).toContain("att-protected-line");
    expect(lines[0]!.className).toContain("flag");
    expect(lines[3]!.className).toContain("att-slot-line");
    expect(screen.getByText("Read only")).toBeTruthy();
  });

  test("test_715_comparing_with_an_earlier_attempt_shows_the_changed_lines", async () => {
    const getCodeAttemptDiff = vi.fn(async () => DIFF);
    install({ getCodeAttemptDiff });
    renderPanel();
    await screen.findByRole("list", { name: "Content of app/skill.py" });
    fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "0" } });
    const diff = await screen.findByRole("figure", { name: "Changes from first generation to correction 1" });
    expect(diff.querySelector(".att-d-del")!.textContent).toContain("-import os");
    expect(diff.querySelector(".att-d-add")!.textContent).toContain("+import json");
    expect(getCodeAttemptDiff).toHaveBeenCalledWith("w1", SUB, 0, 1, "app/skill.py");
  });

  test("test_715_a_file_that_did_not_change_says_so", async () => {
    install({ getCodeAttemptDiff: async () => ({ ...DIFF, changed: false, unified: "" }) });
    renderPanel();
    await screen.findByRole("list", { name: "Content of app/skill.py" });
    fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "0" } });
    expect((await screen.findByText(/This file is the same in first generation and correction 1/)).textContent).toBeTruthy();
  });

  test("test_715_another_sub_skill_can_be_picked", async () => {
    const getCodeAttempts = vi.fn(async () => ATTEMPTS);
    install({ getCodeAttempts });
    renderPanel();
    await screen.findByRole("list", { name: "Attempts" });
    fireEvent.change(screen.getByLabelText("Sub-Skill"), { target: { value: "s2" } });
    await waitFor(() => expect(getCodeAttempts).toHaveBeenCalledWith("w1", "s2"));
  });

  test("test_715_an_attempt_that_stored_no_files_says_why", async () => {
    install({ getCodeAttempts: async () => ({ items: [{ ...ATTEMPTS.items[0]!, files: [] }] }) });
    renderPanel();
    expect(await screen.findByText("No code stored for this attempt")).toBeTruthy();
  });

  test("test_715_no_attempts_yet_is_the_empty_state", async () => {
    install({ getCodeAttempts: async () => ({ items: [] }) });
    renderPanel();
    expect(await screen.findByText("No code attempts yet")).toBeTruthy();
  });

  test("test_715_a_failed_load_shows_the_detail_and_a_retry", async () => {
    install({ getCodeAttempts: async () => { throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "the store is down" } }); } });
    renderPanel();
    expect((await screen.findByText("the store is down")).textContent).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("test_715_another_tenants_sub_skill_is_the_forbidden_state", async () => {
    install({ getCodeAttempts: async () => { throw Object.assign(new Error("x"), { statusCode: 404 }); } });
    renderPanel();
    expect(await screen.findByText("You can't see this Sub-Skill's code")).toBeTruthy();
  });

  test("test_715_no_workflow_is_the_empty_state", async () => {
    install();
    renderPanel("");
    expect(await screen.findByText("No code attempts yet")).toBeTruthy();
  });
});
