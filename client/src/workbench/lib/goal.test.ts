import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { addFilesLocked, canAddFiles, getGoal, goalStage, parseGoal, resetGoal, saveGoal, skipGoal } from "./goal";

const open = (project: string) => window.history.pushState(null, "", `/projects/${project}/workbench`);

beforeEach(() => {
  localStorage.clear();
  open("p-reset");
  resetGoal();
});
afterEach(() => localStorage.clear());

describe("the project goal is kept in this browser, per project, with nothing invented from it (#719)", () => {
  test("test_719_a_new_project_has_no_goal_and_uploading_is_locked", () => {
    open("p-new");
    expect(getGoal().saved).toBe(false);
    expect(addFilesLocked()).toBe(true);
    expect(goalStage(getGoal(), 0)).toBe(1);
  });

  test("test_719_saving_a_goal_keeps_the_text_as_typed_and_opens_the_files_pane", () => {
    open("p-a");
    saveGoal("  Build an AI agent that automates quoting for sales managers in India ");
    const s = getGoal();
    expect(s.text).toBe("Build an AI agent that automates quoting for sales managers in India");
    expect(s.goal).toEqual({ what: "quoting", who: "sales managers", where: "India" });
    expect(canAddFiles(s)).toBe(true);
    expect(addFilesLocked()).toBe(false);
    expect(goalStage(s, 0)).toBe(2);
    expect(goalStage(s, 3)).toBe(3);
  });

  test("test_719_the_goal_holds_only_what_was_typed_no_generated_draft", () => {
    open("p-b");
    saveGoal("RFQ responses for procurement teams in Europe");
    expect(Object.keys(getGoal()).sort()).toEqual(["goal", "saved", "text"]);
  });

  test("test_719_each_project_has_its_own_goal", () => {
    open("p-one");
    saveGoal("Quoting for sales in India");
    open("p-two");
    expect(getGoal().saved).toBe(false);
    saveGoal("Quality engineering for plant managers in Europe");
    open("p-one");
    expect(getGoal().text).toBe("Quoting for sales in India");
    expect(localStorage.getItem("bam-goal-v3:p-two")).toContain("Quality engineering");
  });

  test("test_719_a_project_that_already_has_work_skips_the_goal_and_resumes", () => {
    open("p-work");
    skipGoal();
    expect(getGoal().saved).toBe(true);
    expect(getGoal().text).toBe("");
    expect(addFilesLocked()).toBe(false);
  });

  test("test_719_skipping_never_replaces_a_goal_that_was_typed", () => {
    open("p-keep");
    saveGoal("Quoting for sales in India");
    skipGoal();
    expect(getGoal().text).toBe("Quoting for sales in India");
  });

  test("test_719_a_blank_goal_is_not_saved", () => {
    open("p-blank");
    saveGoal("   ");
    expect(getGoal().saved).toBe(false);
  });

  test("test_719_free_text_without_for_and_in_still_parses", () => {
    expect(parseGoal("Lead generation")).toEqual({ what: "Lead generation", who: "your team", where: "your market" });
  });
});
