import { describe, expect, test } from "vitest";

import type { CodeAttemptSummary } from "@/contracts";

import { attemptLabel, comparable, diffKind, inSlot, newestFirst } from "./codeAttempts";

const att = (attempt: number, files = 1): CodeAttemptSummary => ({
  build_no: 1, cycle: 0, attempt, status: "PASSED", template_name: "skill", template_version: "1.0.0", files: Array.from({ length: files }, (_, i) => ({ path: `f${i}.py`, sha256: "a".repeat(64) })), findings: [],
});

describe("the code attempt helpers (#715)", () => {
  test("test_715_attempt_zero_is_the_first_generation_and_the_rest_are_corrections", () => {
    expect(attemptLabel(0)).toBe("First generation");
    expect(attemptLabel(2)).toBe("Correction 2");
  });

  test("test_715_the_newest_attempt_comes_first", () => {
    expect(newestFirst([att(0), att(2), att(1)]).map(a => a.attempt)).toEqual([2, 1, 0]);
  });

  test("test_715_a_line_is_generated_body_only_inside_a_slot", () => {
    const slots = [{ start: 5, end: 8 }, { start: 12, end: 12 }];
    expect(inSlot(4, slots)).toBe(false);
    expect(inSlot(5, slots)).toBe(true);
    expect(inSlot(8, slots)).toBe(true);
    expect(inSlot(9, slots)).toBe(false);
    expect(inSlot(12, slots)).toBe(true);
  });

  test("test_715_only_other_attempts_with_stored_files_can_be_compared", () => {
    expect(comparable([att(0), att(1, 0), att(2)], 2).map(a => a.attempt)).toEqual([0]);
  });

  test("test_715_a_diff_line_has_a_role_from_its_first_characters", () => {
    expect(diffKind("+++ b/a.py")).toBe("meta");
    expect(diffKind("--- a/a.py")).toBe("meta");
    expect(diffKind("@@ -1,2 +1,3 @@")).toBe("hunk");
    expect(diffKind("+x = 1")).toBe("add");
    expect(diffKind("-x = 0")).toBe("del");
    expect(diffKind(" x = 2")).toBe("context");
  });
});
