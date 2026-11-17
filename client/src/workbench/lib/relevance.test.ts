import { describe, expect, test } from "vitest";

import type { DocumentRelevance } from "@/contracts";

import { relevanceCell } from "./relevance";

const scored: DocumentRelevance = {
  level: "HIGH",
  score: 88,
  reason: "Lists every product with its unit price.",
  goal_sha256: "a".repeat(64),
  scored_at: "2026-11-17T09:00:00Z",
};

describe("the Upload Confidence cell is the backend's relevance to the goal (#802)", () => {
  test("test_802_t40_a_scored_file_shows_its_level_score_and_reason", () => {
    expect(relevanceCell({ state: "ready", parsed: true, relevance: scored }, true)).toEqual({
      kind: "score",
      level: "HIGH",
      label: "High",
      score: 88,
      reason: "Lists every product with its unit price.",
    });
  });

  test("test_802_t41_a_file_that_is_not_ready_shows_nothing", () => {
    for (const state of ["uploading", "checking", "failed"]) {
      expect(relevanceCell({ state, relevance: scored }, true)).toBeNull();
    }
  });

  test("test_802_t42_without_an_answer_the_cell_says_why", () => {
    expect(relevanceCell({ state: "ready", parsed: true }, false)).toMatchObject({ label: "Set a goal" });
    expect(relevanceCell({ state: "ready", parsed: false }, true)).toMatchObject({ label: "Reading…" });
    expect(relevanceCell({ state: "ready", parsed: true }, true)).toMatchObject({ label: "Scoring…" });
  });

  test("test_802_t43_a_score_from_an_earlier_goal_is_still_shown_until_rescored", () => {
    // The backend re-scores after a goal save; until then the earlier answer is better than none.
    expect(relevanceCell({ state: "ready", parsed: true, relevance: scored }, false)).toMatchObject({ label: "High" });
  });
});
