import { describe, expect, it } from "vitest";

import type {
  ErrorCode,
  ManifestStatus,
  ProblemDetail,
  SbamSkillMetadata,
  SowDocument,
} from "@/contracts";

// E1.2: the generated contracts are importable and strict. Most of the value is compile-time:
// `pnpm typecheck` fails if a field name, enum value or optionality drifts from these usages.

describe("generated contracts (E1.2)", () => {
  it("e1_2_t07 enums are string unions", () => {
    const statuses: ManifestStatus[] = ["DRAFT", "VALIDATING", "APPROVED", "SUPERSEDED"];
    const code: ErrorCode = "NOT_FOUND";
    // @ts-expect-error unknown status is not part of the union
    const bad: ManifestStatus = "CERTIFIED";
    expect(statuses).toHaveLength(4);
    expect([code, bad]).toHaveLength(2);
  });

  it("e1_2_t07 skill metadata carries the SBAM literals", () => {
    const meta: SbamSkillMetadata = {
      macro_category: "MS01_Investor_Registration_Access",
      skill_id: "MS01.1_Investor_Auth_Manager",
      skill_name: "Investor OTP & Access Verifier",
      skill_type: "Deterministic_Loopy_MCP",
      reusable: true,
      version: "1.0.0",
      status: "APPROVED",
      owner: "Platform Operations",
      created_at: "2026-06-29T12:00:00Z",
      updated_at: "2026-06-29T12:00:00Z",
    };
    expect(meta.skill_type).toBe("Deterministic_Loopy_MCP");
  });

  it("e1_2_problem_detail_and_sow_are_exported", () => {
    const problem: ProblemDetail = {
      type: "about:blank",
      title: "Not found",
      status: 404,
      code: "NOT_FOUND",
      correlation_id: "c-1",
    };
    const sections: (keyof SowDocument)[] = ["overview", "modules", "features"];
    expect(problem.code).toBe("NOT_FOUND");
    expect(sections).toContain("features");
  });
});
