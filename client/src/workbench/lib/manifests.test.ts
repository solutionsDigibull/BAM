import { describe, expect, test } from "vitest";

import { hashOf, originOf, pretty } from "./manifests";

describe("the manifest helpers (#715)", () => {
  test("test_715_the_hash_is_the_one_the_manifest_carries_else_the_rows", () => {
    expect(hashOf({ sha256: "row", manifest: { governance: { deterministic_hash: "gov" } } })).toBe("gov");
    expect(hashOf({ sha256: "row", manifest: { integrity: { content_sha256: "int" } } })).toBe("int");
    expect(hashOf({ sha256: "row", manifest: {} })).toBe("row");
    expect(hashOf({ sha256: "row", manifest: { governance: { deterministic_hash: 7 } } })).toBe("row");
  });

  test("test_715_keys_are_sorted_at_every_level_and_arrays_keep_their_order", () => {
    const text = pretty({ b: 1, a: { d: [3, { z: 1, y: 2 }], c: 0 } });
    expect(Object.keys(JSON.parse(text))).toEqual(["a", "b"]);
    expect(text.indexOf('"c"')).toBeLessThan(text.indexOf('"d"'));
    expect(text.indexOf('"y"')).toBeLessThan(text.indexOf('"z"'));
    expect(JSON.parse(text).a.d[0]).toBe(3);
  });

  test("test_715_the_origin_names_the_build_and_for_a_sub_skill_the_cycle_and_attempt", () => {
    expect(originOf({ build_no: 3 })).toBe("build 3");
    expect(originOf({ build_no: 3, cycle: 0, attempt: 2 })).toBe("build 3 · cycle 0 · attempt 2");
    expect(originOf({ build_no: 3, cycle: null, attempt: null })).toBe("build 3");
  });
});
