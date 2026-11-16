import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { installGoalApi, type FakeGoalApi } from "./goal.fake";
import { addFilesLocked, canAddFiles, getGoal, goalStage, parseGoal, resetGoal, saveGoal, skipGoal } from "./goal";

const open = (project: string) => window.history.pushState(null, "", `/projects/${project}/workbench`);
/** Opens the project's workbench and waits until its goal has been read from the project. */
const opened = async (project: string) => {
  open(project);
  getGoal();
  await vi.waitFor(() => expect(getGoal().loaded).toBe(true));
};

let api: FakeGoalApi;
beforeEach(() => {
  api = installGoalApi();
  open("p-reset");
  resetGoal();
});
afterEach(() => vi.unstubAllGlobals());

describe("the project goal is stored on the project, with nothing invented from it (#719, #720)", () => {
  test("test_719_a_new_project_has_no_goal_and_uploading_is_locked", async () => {
    await opened("p-new");
    expect(getGoal().saved).toBe(false);
    expect(addFilesLocked()).toBe(true);
    expect(goalStage(getGoal(), 0)).toBe(1);
  });

  test("test_719_saving_a_goal_keeps_the_text_as_typed_and_opens_the_files_pane", async () => {
    await opened("p-a");
    await saveGoal("  Build an AI agent that automates quoting for sales managers in India ");
    const s = getGoal();
    expect(s.text).toBe("Build an AI agent that automates quoting for sales managers in India");
    expect(s.goal).toEqual({ what: "quoting", who: "sales managers", where: "India" });
    expect(canAddFiles(s)).toBe(true);
    expect(addFilesLocked()).toBe(false);
    expect(goalStage(s, 0)).toBe(2);
    expect(goalStage(s, 3)).toBe(3);
  });

  test("test_719_the_goal_holds_only_what_was_typed_no_generated_draft", async () => {
    await opened("p-b");
    await saveGoal("RFQ responses for procurement teams in Europe");
    expect(Object.keys(getGoal()).sort()).toEqual(["goal", "isatvon", "loaded", "saved", "text"]);
  });

  test("test_720_t30_the_goal_is_saved_on_the_project_with_if_match", async () => {
    await opened("p-save");
    await saveGoal("Quoting for sales in India");
    expect(api.goals.get("p-save")?.goal).toBe("Quoting for sales in India");
    const put = api.calls.find((c) => c.method === "PUT");
    expect(put).toEqual({ method: "PUT", project: "p-save", ifMatch: '"1"', body: { goal: "Quoting for sales in India" } });
    expect(getGoal().error).toBeUndefined();
  });

  test("test_720_t31_a_goal_saved_elsewhere_is_read_from_the_project", async () => {
    api.goals.set("p-shared", { goal: "Lead generation for marketing teams in Europe", version: 4 });
    await opened("p-shared");
    expect(getGoal()).toMatchObject({ saved: true, text: "Lead generation for marketing teams in Europe" });
    expect(addFilesLocked()).toBe(false);
  });

  test("test_720_t32_a_stale_etag_reads_the_current_one_and_saves_once_more", async () => {
    await opened("p-stale");
    api.goals.set("p-stale", { goal: "Another FDE's goal", version: 2 }); // saved in another tab meanwhile
    await saveGoal("Quoting for sales in India");
    expect(api.goals.get("p-stale")).toEqual({ goal: "Quoting for sales in India", version: 3 });
    expect(api.calls.filter((c) => c.method === "PUT").map((c) => c.ifMatch)).toEqual(['"1"', '"2"']);
    expect(getGoal().error).toBeUndefined();
  });

  test("test_720_t33_a_refused_save_keeps_the_goal_on_screen_and_says_so", async () => {
    await opened("p-down");
    api.failing.set("p-down", 503);
    await saveGoal("Quoting for sales in India");
    expect(getGoal()).toMatchObject({ saved: true, text: "Quoting for sales in India" });
    expect(getGoal().error).toMatch(/not saved on the project yet/);
  });

  test("test_720_t34_newlines_are_collapsed_to_one_line_as_the_server_stores_it", async () => {
    await opened("p-lines");
    await saveGoal("Quoting for\nsales managers\tin India");
    expect(api.goals.get("p-lines")?.goal).toBe("Quoting for sales managers in India");
  });

  test("test_720_t35_a_goal_over_300_characters_is_not_sent", async () => {
    await opened("p-long");
    await saveGoal("x".repeat(301));
    expect(getGoal().saved).toBe(false);
    expect(getGoal().error).toMatch(/at most 300 characters/);
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
  });

  test("test_719_each_project_has_its_own_goal", async () => {
    await opened("p-one");
    await saveGoal("Quoting for sales in India");
    await opened("p-two");
    expect(getGoal().saved).toBe(false);
    await saveGoal("Quality engineering for plant managers in Europe");
    await opened("p-one");
    expect(getGoal().text).toBe("Quoting for sales in India");
    expect(api.goals.get("p-two")?.goal).toBe("Quality engineering for plant managers in Europe");
  });

  test("test_719_a_project_that_already_has_work_skips_the_goal_and_resumes", async () => {
    await opened("p-work");
    skipGoal();
    expect(getGoal().saved).toBe(true);
    expect(getGoal().text).toBe("");
    expect(addFilesLocked()).toBe(false);
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false); // nothing is written
  });

  test("test_719_skipping_never_replaces_a_goal_that_was_typed", async () => {
    await opened("p-keep");
    await saveGoal("Quoting for sales in India");
    skipGoal();
    expect(getGoal().text).toBe("Quoting for sales in India");
  });

  test("test_719_a_blank_goal_is_not_saved", async () => {
    await opened("p-blank");
    await saveGoal("   ");
    expect(getGoal().saved).toBe(false);
  });

  test("test_798_t10_the_isatvon_template_comes_with_the_saved_goal", async () => {
    await opened("p-tpl");
    expect(getGoal().isatvon).toEqual([]); // no goal yet: nothing to show
    await saveGoal("Quoting for sales in India");
    const [i] = getGoal().isatvon;
    expect(i).toEqual({
      key: "I",
      label: "Instructions",
      text: "You are an FDE Agent operating under ISATVON constraints.\nGoal: Quoting for sales in India",
    });
  });

  test("test_798_t11_a_goal_saved_elsewhere_brings_its_template", async () => {
    api.goals.set("p-tpl2", { goal: "Lead generation for marketing teams in Europe", version: 2 });
    await opened("p-tpl2");
    expect(getGoal().isatvon.map((s) => s.key)).toEqual(["I", "N"]);
  });

  test("test_719_free_text_without_for_and_in_still_parses", () => {
    expect(parseGoal("Lead generation")).toEqual({ what: "Lead generation", who: "your team", where: "your market" });
  });
});
