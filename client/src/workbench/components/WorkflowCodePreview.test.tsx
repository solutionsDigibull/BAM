import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { WorkflowCodeTree, WorkflowFileView } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { WorkflowCodePreview } from "./WorkflowCodePreview";

const SHA = "b".repeat(64);
const TREE: WorkflowCodeTree = {
  workflow_id: "w1",
  groups: [
    {
      kind: "sub_skill", group_id: "s1", label: "MS01.1 Intake", status: "PASSED",
      files: [
        { path: "skill.py", sha256: SHA, tag: "generated", findings: [{ gate: "static", code: "S101", file: "skill.py", line: 2, message: "assert used" }] },
        { path: "vendor/acme@abc123/lib.py", sha256: SHA, tag: "reused", findings: [] },
      ],
      gates: [{ gate: "ast", status: "PASSED" }, { gate: "import", status: "FAILED" }], findings: [],
    },
    {
      kind: "workflow", group_id: "workflow", label: "Workflow", status: null,
      files: [
        { path: "patches/acme@abc123/0001-fix.patch", sha256: SHA, tag: "customized", findings: [] },
        { path: "manifest.json", sha256: SHA, tag: "generated", findings: [] },
      ],
      gates: [], findings: [],
    },
  ],
};
const view = (path: string, content: string, over: Partial<WorkflowFileView> = {}): WorkflowFileView =>
  ({ group_id: "s1", path, tag: "generated", sha256: SHA, content, truncated: false, findings: [], ...over });

const install = (over: Partial<BamApi>) =>
  setBamApi({
    getWorkflowCode: async () => TREE,
    getWorkflowFile: async (_w: string, _g: string, p: string) => view(p, "line one\nassert x\nline three\n"),
    ...over,
  } as unknown as BamApi);
const renderPreview = (id: string | undefined = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <WorkflowCodePreview workflowId={id} />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("Translate shows the whole workflow's stored code, read only (#841)", () => {
  test("test_841_every_file_is_listed_once_with_its_tag_and_the_summary_counts_them", async () => {
    install({});
    renderPreview();
    const nav = await screen.findByRole("navigation", { name: "Workflow files" });
    expect(within(nav).getAllByRole("button")).toHaveLength(4);
    expect(within(nav).getByTitle("vendor/acme@abc123/lib.py").textContent).toContain("Reused");
    expect(within(nav).getByTitle("patches/acme@abc123/0001-fix.patch").textContent).toContain("Customized");
    expect(within(nav).getByTitle("skill.py").textContent).toContain("Generated");
    const summary = screen.getByRole("group", { name: "Files by origin" });
    expect(summary.textContent).toMatch(/4 files/);
    expect(summary.textContent).toMatch(/Reused 1/);
    expect(summary.textContent).toMatch(/Customized 1/);
    expect(summary.textContent).toMatch(/Generated 2/);
  });

  test("test_841_gate_results_sit_beside_the_group_and_a_failed_gate_is_shown_as_failed", async () => {
    install({});
    renderPreview();
    const gates = await screen.findByRole("list", { name: "Gate results for MS01.1 Intake" });
    expect(gates.textContent).toContain("ast · PASSED");
    expect(gates.textContent).toContain("import · FAILED");
    expect(screen.getByText("1 group with findings")).toBeTruthy();
  });

  test("test_841_the_first_file_opens_with_numbered_lines_and_a_finding_marked_on_its_line", async () => {
    install({
      getWorkflowFile: async (_w: string, _g: string, p: string) =>
        view(p, "line one\nassert x\nline three\n", { findings: [{ gate: "static", code: "S101", file: p, line: 2, message: "assert used" }] }),
    });
    renderPreview();
    const code = await screen.findByRole("list", { name: "Content of skill.py" });
    expect(within(code).getAllByRole("listitem")).toHaveLength(3);
    expect(within(code).getByLabelText("1 finding on line 2")).toBeTruthy();
    const findings = screen.getByRole("list", { name: "Findings in this file" });
    expect(findings.textContent).toContain("static S101: assert used");
    fireEvent.click(within(findings).getByRole("button", { name: "line 2" }));
    expect(code.querySelector("li.focus")?.textContent).toContain("assert x");
  });

  test("test_841_choosing_a_file_loads_that_file_with_its_group_and_path", async () => {
    const getWorkflowFile = vi.fn(async (_w: string, _g: string, p: string) => view(p, `# ${p}\n`));
    install({ getWorkflowFile });
    renderPreview();
    await screen.findByRole("list", { name: "Content of skill.py" });
    fireEvent.click(screen.getByTitle("manifest.json"));
    await screen.findByRole("list", { name: "Content of manifest.json" });
    expect(getWorkflowFile).toHaveBeenLastCalledWith("w1", "workflow", "manifest.json");
  });

  test("test_841_a_file_over_the_cap_is_flagged_as_cut", async () => {
    install({ getWorkflowFile: async (_w: string, _g: string, p: string) => view(p, "x\n", { truncated: true }) });
    renderPreview();
    expect(await screen.findByText(/longer than 1 MiB/)).toBeTruthy();
  });

  test("test_841_the_screen_has_no_way_to_change_a_file", async () => {
    install({});
    renderPreview();
    await screen.findByRole("list", { name: "Content of skill.py" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /save|approve|retry|edit|delete/i })).toBeNull();
    expect(screen.getByText("Read only")).toBeTruthy();
  });

  test("test_841_loading_shows_a_skeleton_until_the_tree_arrives", async () => {
    install({ getWorkflowCode: () => new Promise(() => undefined) });
    renderPreview();
    expect(await screen.findByLabelText("Loading the workflow's code")).toBeTruthy();
  });

  test("test_841_a_workflow_with_no_stored_code_shows_the_empty_state", async () => {
    install({ getWorkflowCode: async () => ({ workflow_id: "w1", groups: [{ ...TREE.groups[0]!, files: [], gates: [] }] }) });
    renderPreview();
    expect(await screen.findByText("No code stored yet")).toBeTruthy();
  });

  test("test_841_no_workflow_id_shows_the_no_workflow_state_and_asks_nothing", () => {
    const getWorkflowCode = vi.fn();
    install({ getWorkflowCode });
    renderPreview("");
    expect(screen.getByText("No workflow yet")).toBeTruthy();
    expect(getWorkflowCode).not.toHaveBeenCalled();
  });

  test("test_841_another_tenants_workflow_or_an_unassigned_fde_sees_the_forbidden_state", async () => {
    install({ getWorkflowCode: () => Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 })) });
    renderPreview();
    expect(await screen.findByText("You can't see this workflow's code")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  test("test_841_a_server_error_shows_the_problem_and_retry_asks_again", async () => {
    const getWorkflowCode = vi
      .fn()
      .mockRejectedValueOnce(Object.assign(new Error("The database is down"), { statusCode: 500 }))
      .mockResolvedValue(TREE);
    install({ getWorkflowCode });
    renderPreview();
    expect(await screen.findByText("The database is down")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Workflow files" })).toBeTruthy());
    expect(getWorkflowCode).toHaveBeenCalledTimes(2);
  });

  test("test_841_a_file_that_cannot_be_read_shows_its_own_error_and_keeps_the_list", async () => {
    install({ getWorkflowFile: () => Promise.reject(Object.assign(new Error("Storage is unavailable"), { statusCode: 503 })) });
    renderPreview();
    expect(await screen.findByText("Storage is unavailable")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workflow files" })).toBeTruthy();
  });
});
