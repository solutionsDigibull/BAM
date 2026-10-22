// The ISATVON workbench's data rules (TD §9.3, E8.7), free of AG Grid so they can be tested alone.
// The tree endpoints are #94's; until they land the shapes are sbam_contracts.isatvon_workbench (#307).
import type {
  Editor,
  GridColumn,
  GridGroup,
  RuleViolation,
  SubSkillState,
  WorkbenchMacro,
  WorkbenchSubSkill,
  WorkflowState,
} from "@/contracts";

export const SCHEMA_URL = "schemas/isatvon/1.4";
export const treeUrl = (projectId: string, workflowId: string) =>
  `projects/${projectId}/workflows/${workflowId}/macro-skills`;
export const subSkillUrl = (projectId: string, workflowId: string, subId: string) =>
  `projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}`;

export type MacroRow = {
  kind: "macro";
  id: string;
  macro: WorkbenchMacro;
  count: number;
  expanded: boolean;
};
export type SubRow = { kind: "sub"; id: string; macroId: string; row: WorkbenchSubSkill };
export type InvalidRow = { kind: "invalid"; id: string; reason: string };
export type GridRow = MacroRow | SubRow | InvalidRow;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** A value at a dotted path, or undefined; never throws on a missing or non-object step. */
export function valueAt(source: unknown, path: string): unknown {
  let node: unknown = source;
  for (const key of path.split(".")) {
    if (!isObject(node)) return undefined;
    node = node[key];
  }
  return node;
}

/** What a cell shows, as plain text: objects and lists as JSON, nothing as "". */
export const cellText = (value: unknown): string =>
  value === null || value === undefined
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);

/** A cell's value: platform fields (state, …) live on the row, everything else in the Sub-Skill. */
export const cellValue = (row: WorkbenchSubSkill, column: GridColumn): unknown =>
  column.platform ? valueAt(row, column.field_path) : valueAt(row.sub_skill, column.field_path);

/** The violations that belong to one cell: the same path, or a path inside it. */
export function cellErrors(row: WorkbenchSubSkill, fieldPath: string): RuleViolation[] {
  return (row.validation_errors ?? []).filter(
    (v) =>
      v.path === fieldPath ||
      v.path.startsWith(`${fieldPath}.`) ||
      v.path.startsWith(`${fieldPath}[`),
  );
}

/** Why a Sub-Skill row can't be shown, or null when it is usable. */
function malformed(raw: unknown): string | null {
  if (!isObject(raw)) return "not an object";
  if (typeof raw.id !== "string") return "no id";
  if (!isObject(raw.sub_skill)) return "no sub_skill";
  if (typeof valueAt(raw.sub_skill, "skill_metadata.skill_id") !== "string") return "no skill_id";
  if (!isObject(valueAt(raw.sub_skill, "isatvon_definition"))) return "no isatvon_definition";
  return null;
}

// The same Sub-Skill object gives the same row object, so AG Grid redraws only rows that changed.
const subRows = new WeakMap<WorkbenchSubSkill, SubRow>();

/**
 * The tree as grid rows: each Macro, then (when expanded) its Sub-Skills. Anything the API sends that
 * isn't usable — null, {}, a row without its ISATVON object — becomes an "Invalid data" row, never a crash.
 */
export function toRows(tree: unknown, expanded: ReadonlySet<string>): GridRow[] {
  const macros = isObject(tree) && Array.isArray(tree.macros) ? (tree.macros as unknown[]) : [];
  const rows: GridRow[] = [];
  macros.forEach((m, i) => {
    if (!isObject(m) || typeof m.id !== "string") {
      rows.push({ kind: "invalid", id: `invalid-macro-${i}`, reason: "Macro Skill without an id" });
      return;
    }
    const macro = m as unknown as WorkbenchMacro;
    const subs = Array.isArray(m.sub_skills) ? (m.sub_skills as unknown[]) : [];
    const open = expanded.has(macro.id);
    rows.push({
      kind: "macro",
      id: `macro-${macro.id}`,
      macro,
      count: subs.length,
      expanded: open,
    });
    if (!open) return;
    subs.forEach((s, j) => {
      const problem = malformed(s);
      if (problem) {
        rows.push({
          kind: "invalid",
          id: `invalid-${macro.id}-${j}`,
          reason: `Sub-Skill ${j + 1}: ${problem}`,
        });
      } else {
        const row = s as WorkbenchSubSkill;
        let sub = subRows.get(row);
        if (!sub) subRows.set(row, (sub = { kind: "sub", id: row.id, macroId: macro.id, row }));
        rows.push(sub);
      }
    });
  });
  return rows;
}

/** Every Macro id, for "expand all" (the default view). */
export const macroIds = (tree: unknown): string[] =>
  isObject(tree) && Array.isArray(tree.macros)
    ? (tree.macros as unknown[]).flatMap((m) =>
        isObject(m) && typeof m.id === "string" ? [m.id] : [],
      )
    : [];

/** The Sub-Skill rows of a tree, skipping what toRows would mark invalid. */
export const subSkills = (tree: unknown): WorkbenchSubSkill[] =>
  isObject(tree) && Array.isArray(tree.macros)
    ? (tree.macros as unknown[]).flatMap((m) =>
        isObject(m) && Array.isArray(m.sub_skills)
          ? (m.sub_skills as unknown[]).filter((s): s is WorkbenchSubSkill => malformed(s) === null)
          : [],
      )
    : [];

/** The columns, only ever from the published metadata: a key on a row never makes a column. */
export const metadataColumns = (groups: GridGroup[] | undefined): GridColumn[] =>
  (groups ?? []).flatMap((g) => g.children ?? []);

/** Edits are offered only while the Workflow is in review; anything else is read-only (TD §14). */
export const isEditable = (state: WorkflowState | undefined) => state === "ISATVON_REVIEW";

export const STATE_COLOR: Partial<Record<SubSkillState, string>> = {
  DRAFT: "default",
  VALID: "green",
  INVALID: "red",
  BUILDING: "processing",
  CORRECTING: "processing",
  TESTING: "processing",
  CERTIFIED: "purple",
  FROZEN: "purple",
  NEEDS_FDE_REVIEW: "warning",
  TESTING_BLOCKED: "warning",
  BUILD_ERROR: "error",
  DEPENDENCY_FAILED: "error",
};

const EDITORS: ReadonlySet<Editor> = new Set([
  "text",
  "largeText",
  "number",
  "boolean",
  "json",
  "expression",
  "stringList",
  "select",
]);

/** A cell is editable only in review, unlocked, on a model field the metadata marks editable. */
export const canEdit = (column: GridColumn, state: WorkflowState | undefined, locked: boolean) =>
  isEditable(state) && !locked && column.editable && !column.platform && EDITORS.has(column.editor);

/** The text an editor starts from: JSON for objects, a line per item for string lists. */
export function toEditText(column: GridColumn, value: unknown): unknown {
  if (column.editor === "stringList") return Array.isArray(value) ? value.join("\n") : "";
  if (column.editor === "json") return value === undefined ? "" : JSON.stringify(value, null, 2);
  return value;
}

const balanced = (text: string) => {
  const open: string[] = [];
  const pairs: Record<string, string> = { ")": "(", "]": "[", "}": "{" };
  for (const ch of text) {
    if ("([{".includes(ch)) open.push(ch);
    else if (ch in pairs && open.pop() !== pairs[ch]) return false;
  }
  return open.length === 0;
};

/** The client-side check before a PATCH (E8.7); the server still revalidates everything. */
export function parseEdit(
  column: GridColumn,
  raw: unknown,
): { value: unknown } | { error: string } {
  const text = typeof raw === "string" ? raw.trim() : raw;
  const empty = text === "" || text === null || text === undefined;
  if (empty && column.editor !== "boolean")
    return column.required ? { error: `${column.header} is required` } : { value: null };
  switch (column.editor) {
    case "number": {
      const n = Number(text);
      if (!Number.isFinite(n)) return { error: `${column.header} must be a number` };
      if (column.type === "integer" && !Number.isInteger(n))
        return { error: `${column.header} must be a whole number` };
      // Out of min/max is still sent: range is an ISATVON rule, so the server stores the Sub-Skill as
      // INVALID with its own message (E8.7-T04) instead of the edit vanishing client-side.
      return { value: n };
    }
    case "boolean":
      return { value: raw === true };
    case "select":
      return column.enum && !column.enum.includes(String(text))
        ? { error: `${column.header} must be one of ${column.enum.join(", ")}` }
        : { value: text };
    case "json":
      try {
        return { value: JSON.parse(String(text)) };
      } catch {
        return { error: `${column.header} is not valid JSON` };
      }
    case "expression":
      return balanced(String(text))
        ? { value: text }
        : { error: `${column.header} has unbalanced brackets` };
    case "stringList":
      return {
        value: String(text)
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean),
      };
    default:
      return { value: text };
  }
}

/** The tree with one Sub-Skill replaced; every other object keeps its identity (one row refreshes). */
export function replaceSub(tree: unknown, next: WorkbenchSubSkill): unknown {
  if (!isObject(tree) || !Array.isArray(tree.macros)) return tree;
  return {
    ...tree,
    macros: (tree.macros as unknown[]).map((m) =>
      isObject(m) &&
      Array.isArray(m.sub_skills) &&
      m.sub_skills.some((s) => isObject(s) && s.id === next.id)
        ? {
            ...m,
            sub_skills: (m.sub_skills as unknown[]).map((s) =>
              isObject(s) && s.id === next.id ? next : s,
            ),
          }
        : m,
    ),
  };
}

/** Edit errors the server hasn't stored (client check, 422), shown on the cell like its own violations. */
export type LocalErrors = Record<string, RuleViolation[]>;

export function withLocal(tree: unknown, local: LocalErrors): unknown {
  let out = tree;
  for (const row of subSkills(tree)) {
    const extra = local[row.id];
    if (extra?.length)
      out = replaceSub(out, {
        ...row,
        validation_errors: [...(row.validation_errors ?? []), ...extra],
      });
  }
  return out;
}
