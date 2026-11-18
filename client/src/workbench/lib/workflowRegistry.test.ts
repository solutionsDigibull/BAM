import { describe, expect, test } from "vitest";

import type { RegisteredWorkflow } from "@/contracts";

import {
  isForbidden, isNotRegistered, isSuperseded, isValidVersion, shortDigest, STATUS_LABEL, statusTone, toolCounts,
} from "./workflowRegistry";

const tool = (name: string, role: "action" | "check") => ({ name, skill_id: "MS01.1", role, input_schema: {}, output_schema: {} });

describe("the registry entry helpers (#849)", () => {
  test("test_849_a_version_is_MAJOR_dot_0_dot_PATCH", () => {
    for (const ok of ["1.0.0", "2.0.14", " 1.0.2 "]) expect(isValidVersion(ok)).toBe(true);
    for (const bad of ["", "1.2.0", "1.0", "v1.0.0", "1.0.0-rc1", "a.0.1", "1.0.0; drop"]) expect(isValidVersion(bad)).toBe(false);
  });

  test("test_849_404_means_not_registered_and_403_means_forbidden", () => {
    expect(isNotRegistered({ statusCode: 404 })).toBe(true);
    expect(isNotRegistered({ statusCode: 403 })).toBe(false);
    expect(isForbidden({ statusCode: 403 })).toBe(true);
    expect(isForbidden({ statusCode: 404 })).toBe(false);
    for (const e of [null, undefined, new Error("x"), { statusCode: 500 }]) {
      expect(isNotRegistered(e)).toBe(false);
      expect(isForbidden(e)).toBe(false);
    }
  });

  test("test_849_status_is_current_or_superseded_and_only_the_current_one_is_ok", () => {
    expect(STATUS_LABEL.REGISTERED).toBe("Current");
    expect(STATUS_LABEL.SUPERSEDED).toBe("Superseded");
    expect(statusTone("REGISTERED")).toBe("ok");
    expect(statusTone("SUPERSEDED")).toBe("muted");
    expect(isSuperseded({ status: "SUPERSEDED" })).toBe(true);
    expect(isSuperseded({ status: "REGISTERED" })).toBe(false);
  });

  test("test_849_a_digest_is_shortened_with_or_without_its_prefix", () => {
    const hex = "0123456789abcdef".repeat(4);
    expect(shortDigest(hex)).toBe("sha256:0123456789ab");
    expect(shortDigest(`sha256:${hex}`)).toBe("sha256:0123456789ab");
  });

  test("test_849_tools_are_counted_by_role", () => {
    const tools = [tool("MS01_1__action", "action"), tool("MS01_1__check", "check"), tool("MS01_2__action", "action"), tool("MS01_2__check", "check")];
    expect(toolCounts(tools as unknown as RegisteredWorkflow["tools"])).toEqual({ action: 2, check: 2 });
  });
});
