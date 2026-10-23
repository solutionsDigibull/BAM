import { describe, expect, test } from "vitest";

import type { SowVersionSummary } from "@/contracts";

import { approveBlocked, approveErrorMessage, approveFailure } from "./sow-api";

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
