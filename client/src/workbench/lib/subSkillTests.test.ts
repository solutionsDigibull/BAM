import { describe, expect, test } from "vitest";

import { kindLabel, passText, resultsByCase, runTone, sandboxLogsUrl } from "./subSkillTests";

describe("the test helpers (#715)", () => {
  test("test_715_a_run_reads_as_passed_of_total_and_no_assertions_is_said_so", () => {
    expect(passText({ passed: 3, total: 5 })).toBe("3 of 5 passed");
    expect(passText({ passed: 0, total: 0 })).toBe("No assertions");
  });

  test("test_715_run_tones_follow_the_state", () => {
    expect(runTone("PASS")).toBe("ok");
    expect(runTone("FAIL")).toBe("err");
    expect(runTone("BLOCKED")).toBe("warn");
    expect(runTone("NON_CERTIFYING")).toBe("muted");
  });

  test("test_715_the_assertions_are_grouped_by_case_and_a_case_passes_only_if_all_do", () => {
    const rows = resultsByCase({
      results: [
        { case: "a", type: "terminal_state", passed: true },
        { case: "b", type: "output_schema", passed: true },
        { case: "a", type: "ui_updates", passed: false, message: "no update" },
      ],
    });
    expect(rows.map(r => [r.name, r.passed, r.results.length])).toEqual([["a", false, 2], ["b", true, 1]]);
  });

  test("test_715_a_case_kind_reads_in_words_and_an_unknown_one_is_shown_as_is", () => {
    expect(kindLabel("PASS_AFTER_RETRY")).toBe("Passes after a retry");
    expect(kindLabel("NEW_KIND")).toBe("NEW_KIND");
  });

  test("test_715_the_sandbox_log_is_the_apis_download_route_for_that_run", () => {
    expect(sandboxLogsUrl("p1", "w1", "r1")).toBe("/api/proxy/api/v1/projects/p1/workflows/w1/sandbox-runs/r1/logs");
  });
});
