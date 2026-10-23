import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { GridColumn, WorkbenchSubSkill } from "@/contracts";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import {
  applyReport,
  canEdit,
  cellErrors,
  cellText,
  cellValue,
  generateBlocked,
  isEditable,
  metadataColumns,
  parseEdit,
  replaceSub,
  subSkills,
  toEditText,
  toRows,
  valueAt,
  withLocal,
} from "./workbench-api";
import { EMPTY_MESSAGE, WorkbenchScreen } from "./workbench-screen";

// jsdom can't lay out AG Grid's virtual rows; the rendered grid is covered by e2e/isatvon-workbench.spec.ts.
vi.mock("next/dynamic", () => ({
  default: () =>
    function GridStub({ rows }: { rows: { kind: string }[] }) {
      return <div data-testid="grid-stub">{rows.map((r) => r.kind).join(",")}</div>;
    },
}));

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const TREE = `GET /projects/${P}/workflows/${W}/macro-skills`;
const SCHEMA = "GET /schemas/isatvon/1.4";

const row = (over: Partial<WorkbenchSubSkill> = {}): WorkbenchSubSkill =>
  ({
    id: "s1",
    macro_id: "m1",
    state: "VALID",
    content_sha256: "a".repeat(64),
    validation_errors: [],
    sub_skill: {
      skill_metadata: {
        macro_category: "MS01",
        skill_id: "MS01.1_intake",
        skill_name: "Intake",
        rule_refs: [],
      },
      isatvon_definition: { v_variables: { max_iterations: 3 } },
    },
    ...over,
  }) as unknown as WorkbenchSubSkill;

const macro = (id: string, subs: unknown[]) => ({
  id,
  macro_category: "MS01",
  name: "Intake",
  description: "",
  order: 0,
  sub_skills: subs,
});

const tree = (macros: unknown[], workflow_state = "ISATVON_REVIEW") => ({
  workflow_id: W,
  workflow_state,
  sow_approved: true,
  macros,
});

const column = (field_path: string, platform = false, over: Partial<GridColumn> = {}) =>
  ({
    field_path,
    platform,
    header: field_path,
    type: "string",
    editor: "text",
    required: true,
    editable: true,
    width: 100,
    ...over,
  }) as GridColumn;
const ed = (editor: GridColumn["editor"], over: Partial<GridColumn> = {}) =>
  column("x", false, { header: "X", editor, ...over });

afterEach(() => vi.unstubAllGlobals());

describe("workbench data rules (E8.7)", () => {
  test("valueAt is null-safe at every step", () => {
    expect(valueAt({ a: { b: 1 } }, "a.b")).toBe(1);
    expect(valueAt({ a: null }, "a.b")).toBeUndefined();
    expect(valueAt(null, "a")).toBeUndefined();
    expect(valueAt({ a: [1] }, "a.b")).toBeUndefined();
  });

  test("cells are text: objects as JSON, nothing as empty", () => {
    expect(cellText({ x: 1 })).toBe('{"x":1}');
    expect(cellText(null)).toBe("");
    expect(cellText("<img src=x onerror=alert(1)>")).toBe("<img src=x onerror=alert(1)>");
  });

  test("platform fields come from the row, model fields from the Sub-Skill", () => {
    const r = row({ state: "INVALID" });
    expect(cellValue(r, column("state", true))).toBe("INVALID");
    expect(cellValue(r, column("isatvon_definition.v_variables.max_iterations"))).toBe(3);
  });

  test("a violation marks its own cell and the cells it is inside", () => {
    const r = row({
      validation_errors: [
        { rule: 5, path: "isatvon_definition.v_variables.max_iterations", message: "1-10" },
      ],
    });
    expect(cellErrors(r, "isatvon_definition.v_variables.max_iterations")).toHaveLength(1);
    expect(cellErrors(r, "isatvon_definition.v_variables")).toHaveLength(1);
    expect(cellErrors(r, "isatvon_definition.v_variable")).toHaveLength(0);
  });

  test.each([[null], [{}], [[]], ["x"], [{ macros: null }]])("e8_7_t02 %j gives no rows", (bad) => {
    expect(toRows(bad, new Set())).toEqual([]);
    expect(subSkills(bad)).toEqual([]);
  });

  test("e8_7_t02 malformed rows become 'invalid' rows, good ones stay", () => {
    const noIsatvon = {
      ...row({ id: "s2" }),
      sub_skill: { skill_metadata: { skill_id: "MS01.2_x" } },
    };
    const rows = toRows(
      tree([macro("m1", [row(), noIsatvon, null]), { name: "no id" }]),
      new Set(["m1"]),
    );
    expect(rows.map((r) => r.kind)).toEqual(["macro", "sub", "invalid", "invalid", "invalid"]);
    expect(rows[2]).toMatchObject({ reason: "Sub-Skill 2: no isatvon_definition" });
    expect(subSkills(tree([macro("m1", [row(), noIsatvon])]))).toHaveLength(1);
  });

  test("a collapsed macro hides its Sub-Skills but keeps its count", () => {
    const rows = toRows(tree([macro("m1", [row(), row({ id: "s2" })])]), new Set());
    expect(rows).toEqual([expect.objectContaining({ kind: "macro", count: 2, expanded: false })]);
  });

  test("e8_7_t06 columns are the metadata's, whatever the rows hold", () => {
    expect(
      metadataColumns([
        { id: "v", header: "V", description: "", children: [column("a"), column("b")] },
      ]).map((c) => c.field_path),
    ).toEqual(["a", "b"]);
    expect(metadataColumns(undefined)).toEqual([]);
  });

  test("only ISATVON_REVIEW is editable", () => {
    expect(isEditable("ISATVON_REVIEW")).toBe(true);
    for (const s of ["BUILDING", "CERTIFIED", "FROZEN", undefined] as const)
      expect(isEditable(s)).toBe(false);
  });
});

describe("workbench edits (E8.7)", () => {
  test("only model fields the metadata marks editable, in review, unlocked", () => {
    const c = column("isatvon_definition.v_variables.max_iterations");
    expect(canEdit(c, "ISATVON_REVIEW", false)).toBe(true);
    expect(canEdit(c, "ISATVON_REVIEW", true)).toBe(false);
    expect(canEdit(c, "BUILDING", false)).toBe(false);
    expect(canEdit(column("state", true), "ISATVON_REVIEW", false)).toBe(false);
    expect(canEdit({ ...c, editable: false }, "ISATVON_REVIEW", false)).toBe(false);
    expect(canEdit({ ...c, editor: "readonly" }, "ISATVON_REVIEW", false)).toBe(false);
    expect(canEdit({ ...c, editor: "wysiwyg" as never }, "ISATVON_REVIEW", false)).toBe(false);
  });

  test("numbers: type and whole-number checks; out of range still goes to the server (T04)", () => {
    const n = ed("number", { type: "integer", min: 1, max: 10 });
    expect(parseEdit(n, "3")).toEqual({ value: 3 });
    expect(parseEdit(n, 11)).toEqual({ value: 11 });
    expect(parseEdit(n, "abc")).toEqual({ error: "X must be a number" });
    expect(parseEdit(n, "2.5")).toEqual({ error: "X must be a whole number" });
    expect(parseEdit(n, "")).toEqual({ error: "X is required" });
    expect(parseEdit({ ...n, required: false }, "")).toEqual({ value: null });
  });

  test("json, select, expression, string list and boolean", () => {
    expect(parseEdit(ed("json"), '{"a":1}')).toEqual({ value: { a: 1 } });
    expect(parseEdit(ed("json"), "{a:")).toEqual({ error: "X is not valid JSON" });
    expect(parseEdit(ed("select", { enum: ["A", "B"] }), "B")).toEqual({ value: "B" });
    expect(parseEdit(ed("select", { enum: ["A", "B"] }), "C")).toEqual({
      error: "X must be one of A, B",
    });
    expect(parseEdit(ed("expression"), "len(items[0]) > 2")).toEqual({
      value: "len(items[0]) > 2",
    });
    expect(parseEdit(ed("expression"), "len(items[0) > 2")).toEqual({
      error: "X has unbalanced brackets",
    });
    expect(parseEdit(ed("stringList"), " a \n\n b ")).toEqual({ value: ["a", "b"] });
    expect(parseEdit(ed("boolean"), false)).toEqual({ value: false });
    expect(toEditText(ed("stringList"), ["a", "b"])).toBe("a\nb");
    expect(toEditText(ed("json"), { a: 1 })).toBe('{\n  "a": 1\n}');
  });

  test("replacing one Sub-Skill keeps every other object, so only its row redraws", () => {
    const a = row({ id: "a" });
    const b = row({ id: "b" });
    const other = macro("m2", [b]);
    const t = tree([macro("m1", [a]), other]);
    const next = replaceSub(t, row({ id: "a", state: "INVALID" })) as ReturnType<typeof tree>;
    expect(subSkills(next).map((s) => s.state)).toEqual(["INVALID", "VALID"]);
    expect(next.macros[1]).toBe(other);
    const before = toRows(t, new Set(["m1", "m2"]));
    const after = toRows(next, new Set(["m1", "m2"]));
    expect(after[3]).toBe(before[3]); // b's row object is reused
    expect(after[1]).not.toBe(before[1]);
  });

  test("a local edit error shows on its cell like a server violation", () => {
    const t = tree([macro("m1", [row({ id: "a" })])]);
    const shown = withLocal(t, {
      a: [{ rule: 0, path: "skill_metadata.skill_name", message: "no" }],
    });
    expect(cellErrors(subSkills(shown)[0]!, "skill_metadata.skill_name")).toHaveLength(1);
    expect(withLocal(t, {})).toBe(t);
  });
});

describe("workbench toolbar rules (E8.7)", () => {
  test("e8_7_t05 Generate only when in review, SOW approved and every Sub-Skill VALID", () => {
    const ok = tree([macro("m1", [row(), row({ id: "s2" })])]);
    expect(generateBlocked(ok)).toBeNull();
    expect(generateBlocked({ ...ok, workflow_state: "BUILDING" })).toBe(
      "The Workflow isn't in ISATVON review",
    );
    expect(generateBlocked({ ...ok, sow_approved: false })).toBe("The SOW isn't approved");
    expect(generateBlocked(tree([macro("m1", [])]))).toBe("There are no Sub-Skills");
    expect(generateBlocked(tree([macro("m1", [row(), null])]))).toBe("Some rows hold invalid data");
    expect(generateBlocked(tree([macro("m1", [row(), row({ id: "s2", state: "INVALID" })])]))).toBe(
      "1 Sub-Skill isn't VALID",
    );
    expect(generateBlocked(null)).toBe("The Workflow isn't in ISATVON review");
  });

  test("a validation report updates its rows and leaves the others alone", () => {
    const b = row({ id: "b" });
    const other = macro("m2", [b]);
    const t = tree([macro("m1", [row({ id: "a" })]), other]);
    const v = { rule: 5, path: "isatvon_definition.v_variables.max_iterations", message: "1-10" };
    const next = applyReport(t, {
      sub_skills: [
        { id: "a", state: "INVALID", validation_errors: [v] },
        { id: "gone", state: "VALID" },
      ],
    }) as ReturnType<typeof tree>;
    expect(subSkills(next)[0]).toMatchObject({ state: "INVALID", validation_errors: [v] });
    expect(next.macros[1]).toBe(other);
    expect(applyReport(t, null)).toBe(t);
  });

  test("macro rows offer Regenerate only when actions are on", () => {
    const t = tree([macro("m1", [row()])]);
    expect(toRows(t, new Set(), true)[0]).toMatchObject({ kind: "macro", actions: true });
    expect(toRows(t, new Set())[0]).toMatchObject({ kind: "macro", actions: false });
  });
});

describe("Workbench screen states (E8.7)", () => {
  const schema = { body: { version: "1.4", json_schema: {}, grid: { column_groups: [] } } };

  test("e8_7_t01 an empty tree shows the empty state", async () => {
    fakeApi({ [SCHEMA]: schema, [TREE]: { body: tree([]) } });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText(EMPTY_MESSAGE)).toBeDefined();
  });

  test("the tree renders macro and sub rows; outside review it says read-only", async () => {
    fakeApi({ [SCHEMA]: schema, [TREE]: { body: tree([macro("m1", [row()])], "BUILDING") } });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect((await screen.findByTestId("grid-stub")).textContent).toBe("macro,sub");
    expect(screen.getByText("Read-only: the Workflow is BUILDING.")).toBeDefined();
    expect(screen.getByText("1 Macro Skill · 1 Sub-Skill")).toBeDefined();
  });

  test("e8_7_t05 Generate is disabled and says why while a Sub-Skill is INVALID", async () => {
    fakeApi({
      [SCHEMA]: schema,
      [TREE]: { body: tree([macro("m1", [row({ state: "INVALID" })])]) },
    });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText("Generate is off: 1 Sub-Skill isn't VALID.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Generate" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Validate" })).toBeDefined();
  });

  test("an extraction in progress shows progress, not an empty grid", async () => {
    fakeApi({ [SCHEMA]: schema, [TREE]: { body: tree([], "ISATVON_EXTRACTING") } });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText(/Extracting the ISATVON 1.4 breakdown/)).toBeDefined();
  });

  test("a failed extraction says so", async () => {
    fakeApi({ [SCHEMA]: schema, [TREE]: { body: tree([], "EXTRACTION_FAILED") } });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText("The ISATVON extraction failed")).toBeDefined();
  });

  test("a tree error shows the problem detail with a retry", async () => {
    fakeApi({ [SCHEMA]: schema, [TREE]: problem(503, "UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<WorkbenchScreen projectId={P} workflowId={W} />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });
});
