import { describe, expect, test } from "vitest";

import { entry } from "./test-fixtures";
import {
  cleanPrompts,
  MAX_MICRO,
  promptsOf,
  ruleCategory,
  ruleSkills,
  ruleStatus,
  workflowMap,
} from "./rules-tools";

const skill = (id: string, name: string, next = "") =>
  entry({
    master_type: "SKILLS",
    stable_id: id,
    id: `sk-${id}`,
    payload: { skill_id: id, skill: name, next },
  });
const rule = (id: string, payload: Record<string, string>) =>
  entry({ stable_id: id, id: `r-${id}`, payload });

describe("MUI-5 Rules Engine helpers", () => {
  test("mui5_t01 the workflow map follows the Next chain and counts rules per skill", () => {
    const skills = [
      skill("SK-03", "Quote"),
      skill("SK-01", "Intake", "SK-02"),
      skill("SK-02", "Scrub", "SK-03"),
    ];
    const rules = [
      rule("R1", { skill_ref: "SK-02" }),
      rule("R2", { mapping_enforcement: "Org ORG-1; skills SK-02, SK-03" }),
    ];
    expect(workflowMap(skills, rules)).toEqual([
      { id: "SK-01", name: "Intake", rules: 0 },
      { id: "SK-02", name: "Scrub", rules: 2 },
      { id: "SK-03", name: "Quote", rules: 1 },
    ]);
  });

  test("mui5_t02 a Next loop and skills outside any chain are each listed once", () => {
    const skills = [
      skill("SK-01", "A", "SK-02"),
      skill("SK-02", "B", "SK-01"),
      skill("SK-09", "Z"),
    ];
    expect(workflowMap(skills, []).map((n) => n.id)).toEqual(["SK-09", "SK-01", "SK-02"]);
  });

  test("mui5_t03 category is the first known rule type, else Other", () => {
    expect(
      ruleCategory(rule("R", { rule_type_description: "RULE-X; Validation: check the BOM" })),
    ).toBe("Validation");
    expect(ruleCategory(rule("R", { rule_type_description: "Something else" }))).toBe("Other");
  });

  test("mui5_t04 status is the Status column word, Proposed by default", () => {
    expect(ruleStatus(rule("R", { record_status: "Approved" }))).toBe("Approved");
    expect(ruleStatus(rule("R", { record_status: "approved by ops" }))).toBe("Approved");
    expect(ruleStatus(rule("R", {}))).toBe("Proposed");
    expect(ruleSkills(rule("R", { skill_ref: "sk-4, SK-4" }))).toEqual(["SK-4"]);
  });

  test("mui5_t05 prompts: a blank micro prompt is dropped and the count is capped", () => {
    expect(
      cleanPrompts("sys", [
        { name: "", text: "" },
        { name: "a", text: "" },
      ]),
    ).toEqual({ system: "sys", micro: [{ name: "a", text: "" }] });
    const many = Array.from({ length: MAX_MICRO + 5 }, (_, i) => ({ name: `n${i}`, text: "t" }));
    expect(cleanPrompts("", many).micro).toHaveLength(MAX_MICRO);
    expect(promptsOf(undefined)).toEqual({ system: "", micro: [] });
  });
});
