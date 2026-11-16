import { describe, expect, test } from "vitest";

import { GOAL_PATH, SCREEN_ROUTES, screenByPath } from "./trackerConfig";

describe("the goal screen has its own address (#720)", () => {
  test("test_720_t40_goal_opens_uploads_first_stage", () => {
    expect(GOAL_PATH).toBe("/goal");
    expect(screenByPath(GOAL_PATH)?.id).toBe("1.1");
    expect(SCREEN_ROUTES["1.1"]!.path).toBe("/ingestion/upload"); // Upload keeps its address once a goal is saved
    expect(screenByPath("/ingestion/upload")?.id).toBe("1.1");
  });
});
