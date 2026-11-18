import { describe, expect, test } from "vitest";

import type { CodeFinding, WorkflowCodeGroup, WorkflowCodeTree } from "@/contracts";

import { fileCount, findingText, firstFile, groupHasProblem, isForbidden, placeFindings, splitLines, statusTone, tagCounts } from "./workflowCode";

const finding = (over: Partial<CodeFinding> = {}): CodeFinding => ({ gate: "static", code: "S101", message: "assert used", ...over });
const group = (over: Partial<WorkflowCodeGroup> = {}): WorkflowCodeGroup => ({
  kind: "sub_skill", group_id: "s1", label: "MS01.1", files: [], gates: [], findings: [], ...over,
});
const tree = (groups: WorkflowCodeGroup[]): WorkflowCodeTree => ({ workflow_id: "w1", groups });
const file = (path: string, tag: "reused" | "customized" | "generated", findings: CodeFinding[] = []) => ({ path, sha256: "a".repeat(64), tag, findings });

describe("the workflow code helpers (#841)", () => {
  test("test_841_tag_counts_and_file_count_cover_every_group", () => {
    const t = tree([
      group({ files: [file("a.py", "generated"), file("vendor/x@1/b.py", "reused")] }),
      group({ kind: "workflow", group_id: "workflow", label: "Workflow", files: [file("patches/x@1/0001.patch", "customized")] }),
    ]);
    expect(tagCounts(t)).toEqual({ reused: 1, customized: 1, generated: 1 });
    expect(fileCount(t)).toBe(3);
  });

  test("test_841_the_first_file_is_the_first_file_of_the_first_group_that_has_one", () => {
    const t = tree([group({ group_id: "empty" }), group({ group_id: "s2", files: [file("z.py", "generated")] })]);
    expect(firstFile(t)).toEqual({ group: "s2", path: "z.py" });
    expect(firstFile(tree([group()]))).toBeUndefined();
  });

  test("test_841_status_tones_follow_the_servers_words", () => {
    expect(statusTone("PASSED")).toBe("ok");
    expect(statusTone("FAILED")).toBe("err");
    expect(statusTone("BUILDING")).toBe("warn");
    expect(statusTone("SOMETHING_NEW")).toBe("muted");
    expect(statusTone(null)).toBe("muted");
  });

  test("test_841_a_group_has_a_problem_when_a_finding_or_a_failed_gate_exists", () => {
    expect(groupHasProblem(group())).toBe(false);
    expect(groupHasProblem(group({ findings: [finding()] }))).toBe(true);
    expect(groupHasProblem(group({ files: [file("a.py", "generated", [finding({ file: "a.py", line: 2 })])] }))).toBe(true);
    expect(groupHasProblem(group({ gates: [{ gate: "ast", status: "FAILED" }] }))).toBe(true);
    expect(groupHasProblem(group({ gates: [{ gate: "ast", status: "PASSED" }] }))).toBe(false);
  });

  test("test_841_a_final_newline_does_not_add_an_empty_last_line", () => {
    expect(splitLines("a\nb\n")).toEqual(["a", "b"]);
    expect(splitLines("a\nb")).toEqual(["a", "b"]);
    expect(splitLines("")).toEqual([""]);
  });

  test("test_841_findings_land_on_their_line_and_the_rest_are_unplaced", () => {
    const placed = finding({ line: 2 });
    const past = finding({ line: 99 });
    const noLine = finding();
    const { byLine, unplaced } = placeFindings([placed, past, noLine], 3);
    expect(byLine.get(2)).toEqual([placed]);
    expect(unplaced).toEqual([past, noLine]);
  });

  test("test_841_a_finding_reads_as_gate_code_message", () => {
    expect(findingText(finding())).toBe("static S101: assert used");
  });

  test("test_841_403_and_404_are_the_forbidden_state_and_other_errors_are_not", () => {
    expect(isForbidden({ statusCode: 404 })).toBe(true);
    expect(isForbidden({ statusCode: 403 })).toBe(true);
    expect(isForbidden({ statusCode: 500 })).toBe(false);
    expect(isForbidden(new Error("x"))).toBe(false);
    expect(isForbidden(null)).toBe(false);
  });
});
