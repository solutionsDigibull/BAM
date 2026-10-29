import { describe, expect, test } from "vitest";

import type { SowImpact, SowVersionSummary } from "@/contracts";

import {
  approveBlocked,
  approveErrorMessage,
  approveFailure,
  impactSummary,
  isScopeChange,
  newVersionErrorMessage,
  reasonProblem,
} from "./sow-api";

const v = (version: number, status = "IN_REVIEW", sha256: string | null = "a".repeat(64)) =>
  ({ version, status, sha256 }) as SowVersionSummary;
const err = (statusCode: number, code?: string, detail?: string) => ({
  statusCode,
  message: detail ?? "x",
  problem: { code, detail },
});

describe("SOW approval rules (E7.3)", () => {
  test("only the latest GENERATED / IN_REVIEW version with a hash can be approved", () => {
    expect(approveBlocked(v(2), 2, false)).toBeNull();
    expect(approveBlocked(v(2, "GENERATED"), 2, false)).toBeNull();
    expect(approveBlocked(v(1), 2, false)).toBe("Only the latest version (v2) can be approved");
    expect(approveBlocked(v(2, "APPROVED"), 2, false)).toBe("A APPROVED version can't be approved");
    expect(approveBlocked(v(2, "GENERATION_FAILED", null), 2, false)).toMatch(/can't be approved/);
    expect(approveBlocked(v(2, "IN_REVIEW", null), 2, false)).toBe(
      "This version has no hash to approve",
    );
    expect(approveBlocked(v(2), 2, true)).toBe("A generation is running");
    expect(approveBlocked(undefined, 2, false)).toBe("Open a version first");
  });

  test("e7_3_t02 stale or not-latest is 'reload'; 503 is 'try again'; the rest shows its detail", () => {
    expect(approveFailure(err(409, "SOW_STALE"))).toBe("stale");
    expect(approveFailure(err(409, "SOW_NOT_LATEST"))).toBe("stale");
    expect(approveFailure(err(503, "ORCHESTRATOR_UNAVAILABLE"))).toBe("unavailable");
    expect(approveFailure(err(503))).toBe("unavailable");
    expect(approveFailure(err(409, "PIPELINE_NOT_WAITING"))).toBe("other");
    expect(approveErrorMessage(err(409, "PIPELINE_NOT_WAITING"))).toBe(
      "The pipeline is not waiting for an SOW approval right now.",
    );
    expect(approveErrorMessage(err(403, "FORBIDDEN", "Only a person can approve"))).toBe(
      "Only a person can approve",
    );
  });
});

describe("scope change (E7.3 on E7.2)", () => {
  const impact = (more: Partial<SowImpact> = {}): SowImpact => ({
    version: 4,
    base_version: 3,
    changed_sections: [],
    affected_macro_skills: [],
    affected_sub_skills: [],
    added_modules: [],
    reused_sub_skills_count: 14,
    ...more,
  });

  test("e7_3_t08 the reason is 1-500 characters", () => {
    expect(reasonProblem("")).toBe("Say why the scope changes");
    expect(reasonProblem("   ")).toBe("Say why the scope changes");
    expect(reasonProblem("x")).toBeNull();
    expect(reasonProblem("x".repeat(500))).toBeNull();
    expect(reasonProblem("x".repeat(501))).toBe("At most 500 characters");
  });

  test("only a draft newer than the approved version is a scope change", () => {
    expect(isScopeChange(v(3, "APPROVED"), v(4))).toBe(true);
    expect(isScopeChange(v(3, "APPROVED"), v(3, "APPROVED"))).toBe(false);
    expect(isScopeChange(null, v(1))).toBe(false); // a first approval has nothing to compare to
    expect(isScopeChange(v(3, "SUPERSEDED"), v(4, "APPROVED"))).toBe(false);
  });

  test("the summary counts what the API listed; zero affected says nothing is rebuilt", () => {
    expect(impactSummary(impact())).toBe(
      "No Sub-Skills need rebuild, 0 Macro Skills affected, 14 reused",
    );
    expect(
      impactSummary(
        impact({
          affected_sub_skills: [
            { skill_id: "MS01.2", reason: "x" },
            { skill_id: "MS01.3", reason: "y" },
          ],
          affected_macro_skills: [{ macro_category: "MS01", reason: "z" }],
          added_modules: ["M9"],
        }),
      ),
    ).toBe("2 Sub-Skills to rebuild, 1 Macro Skill affected, 1 new module, 14 reused");
  });

  test("a second draft or nothing approved says what to do", () => {
    expect(newVersionErrorMessage(err(409, "DRAFT_EXISTS"))).toBe(
      "A new version is already open: edit or approve it.",
    );
    expect(newVersionErrorMessage(err(409, "NO_APPROVED_SOW"))).toBe(
      "Nothing is approved yet: approve a version first.",
    );
    expect(newVersionErrorMessage(err(422, "VALIDATION_ERROR", "reason too long"))).toBe(
      "reason too long",
    );
  });
});
