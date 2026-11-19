import { describe, expect, test } from "vitest";

import type { BuildRow, BuildsResponse } from "@/contracts";

import { canRetry, gateStatus, gateTone, hasFailedGate, pollMs, problemText, reachedGates, stateLabel, stateTone, summary } from "./workflowBuilds";

const row = (over: Partial<BuildRow> = {}): BuildRow => ({
  sub_skill_id: "s1", skill_id: "MS01.1", skill_name: "Verify payment", state: "BUILDING", stage: "ast_gate", attempt: 0, gates: {}, ...over,
});

describe("the build helpers (#715)", () => {
  test("test_715_a_gate_that_has_not_been_reached_is_waiting_and_not_listed", () => {
    const r = row({ gates: { ISATVON_CONTRACT: "PASSED", RUFF: "FAILED" } });
    expect(gateStatus(r, "AST_SYNTAX")).toBe("PENDING");
    expect(reachedGates(r).map(g => g.name)).toEqual(["ISATVON_CONTRACT", "RUFF"]);
    expect(hasFailedGate(r)).toBe(true);
    expect(hasFailedGate(row())).toBe(false);
  });

  test("test_715_gate_tones_follow_the_status", () => {
    expect(gateTone("PASSED")).toBe("ok");
    expect(gateTone("FAILED")).toBe("err");
    expect(gateTone("RUNNING")).toBe("warn");
    expect(gateTone("SKIPPED")).toBe("muted");
  });

  test("test_715_retry_is_offered_only_to_a_sub_skill_waiting_for_the_fde", () => {
    expect(canRetry("NEEDS_FDE_REVIEW")).toBe(true);
    expect(canRetry("TESTING_BLOCKED")).toBe(true);
    expect(canRetry("BUILDING")).toBe(false);
    expect(canRetry("CERTIFIED")).toBe(false);
    expect(canRetry(undefined)).toBe(false);
  });

  test("test_715_states_read_in_plain_words", () => {
    expect(stateLabel("NEEDS_FDE_REVIEW")).toBe("Needs your review");
    expect(stateLabel("BUILD_ERROR")).toBe("Build error");
    expect(stateLabel("NEW_STATE")).toBe("NEW_STATE");
    expect(stateTone("NEEDS_FDE_REVIEW")).toBe("warn");
    expect(stateTone("CERTIFIED")).toBe("ok");
    expect(stateTone("BUILD_ERROR")).toBe("err");
  });

  test("test_715_the_builds_are_polled_only_while_something_is_active", () => {
    const b = (active: boolean) => ({ workflow_id: "w", workflow_state: "BUILDING", active, sub_skills: [] }) as BuildsResponse;
    expect(pollMs(b(true))).toBe(3000);
    expect(pollMs(b(false))).toBe(false);
    expect(pollMs(undefined)).toBe(false);
  });

  test("test_715_the_summary_counts_failed_gates_and_sub_skills_waiting_for_the_fde", () => {
    const rows = [row(), row({ gates: { RUFF: "FAILED" } }), row({ state: "NEEDS_FDE_REVIEW" })];
    expect(summary(rows)).toEqual({ total: 3, failed: 1, review: 1 });
  });

  test("test_715_a_refusal_is_shown_in_the_servers_words", () => {
    expect(problemText({ problem: { detail: "Not in review", title: "Conflict" } })).toBe("Not in review");
    expect(problemText({ problem: { title: "Conflict" } })).toBe("Conflict");
    expect(problemText(new Error("boom"))).toBe("boom");
    expect(problemText(null)).toBe("The request was not accepted.");
  });
});
