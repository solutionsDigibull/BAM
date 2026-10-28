import { describe, expect, test } from "vitest";

import type { WorkbenchSubSkill } from "@/contracts";

import { cycleMessage, ioGraph, ioLayout } from "./io-graph";

const sub = (id: string, inputs: string[], outputs: Record<string, unknown>): WorkbenchSubSkill =>
  ({
    id,
    macro_id: "m1",
    state: "VALID",
    content_sha256: "a".repeat(64),
    validation_errors: [],
    sub_skill: {
      skill_metadata: { skill_id: id, skill_name: id, macro_category: "MS01_X" },
      isatvon_definition: {
        s_source: { required_inputs: inputs },
        o_outcome: { json_schema: outputs },
      },
    },
  }) as unknown as WorkbenchSubSkill;

describe("I/O dependency graph (E8.8)", () => {
  test("an edge runs from an output field to the Sub-Skill that needs it", () => {
    const g = ioGraph([
      sub("MS01.1", ["mobile"], { otp_ok: "boolean" }),
      sub("MS01.2", ["otp_ok", "state_handoff_id"], { kyc: "string" }),
    ]);
    expect(g.edges.map((e) => [e.source, e.target, e.field, e.cycle])).toEqual([
      ["MS01.1", "MS01.2", "otp_ok", false],
      ["MS01.1", "MS01.2", "state_handoff_id", false],
    ]);
    expect(g.cycles).toEqual([]);
  });

  test("real JSON Schema outputs are read from properties", () => {
    const g = ioGraph([
      sub("A", [], { type: "object", properties: { token: { type: "string" } } }),
      sub("B", ["token"], {}),
    ]);
    expect(g.edges.map((e) => e.field)).toEqual(["token"]);
  });

  test("E8.8-T07 a cycle MS01.2 -> MS01.3 -> MS01.2 is found and its edges are red", () => {
    const g = ioGraph([
      sub("MS01.1", [], { a: "string" }),
      sub("MS01.2", ["a", "c"], { b: "string" }),
      sub("MS01.3", ["b"], { c: "string" }),
    ]);
    expect(g.cycles).toEqual([["MS01.2", "MS01.3"]]);
    const red = g.edges.filter((e) => e.cycle).map((e) => `${e.source}>${e.target}`);
    expect(red.sort()).toEqual(["MS01.2>MS01.3", "MS01.3>MS01.2"]);
    expect(g.edges.find((e) => e.source === "MS01.1")?.cycle).toBe(false);
    expect(cycleMessage(g, "MS01.3")).toBe("Dependency cycle: MS01.2 → MS01.3 → MS01.2");
    expect(cycleMessage(g, "MS01.1")).toBeNull();
  });

  test("a Sub-Skill with no inputs or outputs is an isolated node", () => {
    const g = ioGraph([sub("A", [], {}), sub("B", ["x"], {})]);
    expect(g.nodes.map((n) => n.id)).toEqual(["A", "B"]);
    expect(g.edges).toEqual([]);
    expect(Object.keys(ioLayout(g))).toEqual(["A", "B"]);
  });

  test("a Sub-Skill never depends on itself", () => {
    const g = ioGraph([sub("A", ["x"], { x: "string" })]);
    expect(g.edges).toEqual([]);
    expect(g.cycles).toEqual([]);
  });
});
