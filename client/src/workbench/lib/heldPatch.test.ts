import { describe, expect, test } from "vitest";

import { ifMatchOf, pathLabel, reasonProblem, showValue } from "./heldPatch";

describe("the held patch helpers (#715)", () => {
  test("test_715_accepting_sends_the_content_hash_quoted_as_if_match", () => {
    expect(ifMatchOf({ content_sha256: "ab".repeat(32) })).toBe(`"${"ab".repeat(32)}"`);
  });

  test("test_715_a_patch_path_reads_as_the_part_of_the_definition_it_touches", () => {
    expect(pathLabel("/isatvon_definition/gate/max")).toBe("gate / max");
    expect(pathLabel("/isatvon_definition")).toBe("the whole definition");
  });

  test("test_715_a_long_value_is_cut_and_never_markup", () => {
    expect(showValue(undefined)).toBe("");
    expect(showValue(null)).toBe("");
    expect(showValue({ a: 1 })).toBe('{"a":1}');
    const cut = showValue("x".repeat(1000));
    expect(cut).toHaveLength(401);
    expect(cut.endsWith("…")).toBe(true);
  });

  test("test_715_rejecting_needs_a_reason_and_accepting_does_not", () => {
    expect(reasonProblem("reject", "  ")).toMatch(/Say why/);
    expect(reasonProblem("reject", "no")).toBeUndefined();
    expect(reasonProblem("accept", "")).toBeUndefined();
    expect(reasonProblem("accept", "x".repeat(501))).toMatch(/at most 500/);
  });
});
