import { describe, expect, test } from "vitest";
import { goalSpans, parseGoal, type GoalSpan } from "./goal";

const PREFIX = "Build an AI agent that automates ";
/** The text of each span, so the cases read as sentences. */
const parts = (value: string) => goalSpans(value).map((s: GoalSpan) => (s ? value.slice(s[0], s[1]) : null));

describe("What / Who / Where cards split the goal like parseGoal (#822)", () => {
  test("822-T01 all three parts", () => {
    expect(parts(PREFIX + "quoting for sales managers in India.")).toEqual(["quoting", "sales managers", "India"]);
  });

  test("822-T02 no ' for ': the whole text is the What", () => {
    expect(parts(PREFIX + "PFMEA report generation")).toEqual(["PFMEA report generation", null, null]);
  });

  test("822-T03 What and Who, no Where yet", () => {
    expect(parts(PREFIX + "quoting for sales managers")).toEqual(["quoting", "sales managers", null]);
  });

  test("822-T04 nothing typed after the fixed phrase: no parts", () => {
    expect(parts(PREFIX)).toEqual([null, null, null]);
    expect(parts(PREFIX + "  .")).toEqual([null, null, null]);
  });

  test("822-T05 spans are offsets into the full box text, after the fixed phrase", () => {
    const value = PREFIX + "quoting for sales managers in India";
    const [what] = goalSpans(value);
    expect(what?.[0]).toBe(PREFIX.length);
  });

  test("822-T06 the parts agree with parseGoal when every part is written", () => {
    const value = PREFIX + "invoice matching for the finance team in Germany";
    const g = parseGoal(value);
    expect(parts(value)).toEqual([g.what, g.who, g.where]);
  });

  test("822-T07 case-insensitive ' FOR ' / ' IN ' and trailing dots are not part of the Where", () => {
    expect(parts(PREFIX + "scheduling FOR planners IN Pune...")).toEqual(["scheduling", "planners", "Pune"]);
  });
});
