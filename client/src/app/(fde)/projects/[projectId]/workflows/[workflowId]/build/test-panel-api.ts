// The test panel's data rules (TD §9.3, E13.7), free of antd so they can be tested alone.
// GET/PUT/POST/DELETE W/sub-skills/{id}/tests… are #125's (sbam_contracts.testgen), GET …/test-runs
// is E13.6's and GET …/test-runs/{run}/traces reads sbam_contracts.harness. The server is the
// authority on every rule here; the panel only says it earlier.
import { Validator, type Schema } from "@cfworker/json-schema";

import type {
  HarnessTrace,
  SubSkillState,
  TestCaseOut,
  TestDatasetOut,
  TestRunOut,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

const sub = (p: string, w: string, s: string) => `projects/${p}/workflows/${w}/sub-skills/${s}`;
export const testsUrl = (p: string, w: string, s: string) => `${sub(p, w, s)}/tests`;
export const casesUrl = (p: string, w: string, s: string) => `${testsUrl(p, w, s)}/cases`;
export const caseUrl = (p: string, w: string, s: string, caseId: string) =>
  `${casesUrl(p, w, s)}/${caseId}`;
export const runsUrl = (p: string, w: string, s: string) => `${sub(p, w, s)}/test-runs`;
export const tracesUrl = (p: string, w: string, s: string, runId: string) =>
  `${runsUrl(p, w, s)}/${runId}/traces`;

export const MIN_CASES = 3; // sbam_contracts.testgen.MIN_CASES
export const MIN_CASES_MESSAGE = "Minimum 3 cases";
export const FROZEN_MESSAGE = "Frozen — create a new version";
export const STALE_MESSAGE =
  "Someone else changed these test cases. Reload to see their version; nothing was overwritten.";
export const NOT_RUN_MESSAGE = "Tests not run yet";
export const NO_TRACE_MESSAGE = "No trace recorded for this run";

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** The dataset of a tests answer, or null: `useCustom` hands back `{}` while loading or on error. */
export const datasetOf = (answer: unknown): TestDatasetOut | null => {
  const dataset = isObject(answer) ? answer.dataset : null;
  return isObject(dataset) && typeof dataset.sha256 === "string"
    ? (dataset as unknown as TestDatasetOut)
    : null;
};

/** The usable cases of a dataset (a case without an id or name is dropped). */
export const casesOf = (dataset: TestDatasetOut | null): TestCaseOut[] =>
  Array.isArray(dataset?.cases)
    ? dataset.cases.filter((c) => isObject(c) && typeof c.id === "string" && !!c.name)
    : [];

/** The Sub-Skill's closed input schema the tests answer carries, or null (then the server decides). */
export const inputSchemaOf = (answer: unknown): Record<string, unknown> | null => {
  const schema = isObject(answer) ? answer.input_schema : null;
  return isObject(schema) ? schema : null;
};

export const runsOf = (answer: unknown): TestRunOut[] =>
  isObject(answer) && Array.isArray(answer.runs)
    ? (answer.runs as unknown[]).filter((r): r is TestRunOut => isObject(r) && !!r.id)
    : [];

export const tracesOf = (answer: unknown): HarnessTrace[] =>
  isObject(answer) && Array.isArray(answer.traces)
    ? (answer.traces as unknown[]).filter(
        (t): t is HarnessTrace => isObject(t) && Array.isArray(t.passes),
      )
    : [];

/** The server refuses a delete that leaves fewer than three (409 MIN_TEST_CASES); so does the panel. */
export const canDelete = (count: number) => count > MIN_CASES;

// The states the server answers 423 for (testgen service): the dataset of a certified build is
// evidence, so the panel hides every edit there.
const LOCKED: ReadonlySet<SubSkillState> = new Set(["CERTIFIED", "FROZEN", "SUPERSEDED"]);
export const isReadOnly = (state: SubSkillState | undefined) => !!state && LOCKED.has(state);

export type CaseResult = "PASSED" | "FAILED" | "NOT_RUN";

/** A case passed in a run only if every one of its assertions passed. */
export function caseResult(run: TestRunOut | undefined, caseName: string): CaseResult {
  const mine = (run?.results ?? []).filter((r) => r.case === caseName);
  if (mine.length === 0) return "NOT_RUN";
  return mine.every((r) => r.passed) ? "PASSED" : "FAILED";
}

/** What the panel says about a refused write; 422 is shown on the fields instead. */
export function writeError(error: ApiError): { kind: "stale" | "message"; text: string } {
  const code = error.problem?.code;
  if (error.statusCode === 412 || error.statusCode === 428)
    return { kind: "stale", text: STALE_MESSAGE };
  if (error.statusCode === 423) return { kind: "message", text: FROZEN_MESSAGE };
  if (error.statusCode === 409 && code === "MIN_TEST_CASES")
    return {
      kind: "message",
      text: `${MIN_CASES_MESSAGE}: a Sub-Skill keeps at least 3 test cases.`,
    };
  return { kind: "message", text: error.message };
}

export type FieldProblem = { path: string; message: string };

/** The 422 field errors of a write, as `/input/amount` → message (problem+json `errors`). */
export const fieldProblems = (error: ApiError): FieldProblem[] =>
  (error.problem?.errors ?? []).map((e) => ({ path: e.path, message: e.message }));

const quoted = (text: string) => /"([^"]+)"/.exec(text)?.[1];

/** The schema at a JSON pointer such as `#/properties/address`, or undefined. */
function at(schema: unknown, pointer: string): unknown {
  return pointer
    .replace(/^#\/?/, "")
    .split("/")
    .filter(Boolean)
    .reduce<unknown>(
      (node, part) =>
        isObject(node) ? node[part.replace(/~1/g, "/").replace(/~0/g, "~")] : undefined,
      schema,
    );
}

// the keywords that only summarise their children's errors; the children name the field
const SUMMARY = new Set(["properties", "items", "prefixItems", "allOf", "$ref", "false"]);
const validators = new WeakMap<object, Validator>();

/**
 * The problems with an input, as the paths the server's 422 uses (`/input`, `/input/amount`).
 * The text must parse to a JSON object; with a schema it must also validate against it. The
 * validator interprets the schema (no `new Function`), so it runs under the portal's CSP.
 */
export function inputProblems(
  text: string,
  schema: Record<string, unknown> | null,
): FieldProblem[] {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return [{ path: "/input", message: `Not valid JSON: ${(e as Error).message}` }];
  }
  if (!isObject(value)) return [{ path: "/input", message: "The input must be a JSON object" }];
  if (!schema) return [];
  let validator = validators.get(schema);
  if (!validator) {
    validator = new Validator(schema as Schema, "2020-12", false);
    validators.set(schema, validator);
  }
  const seen = new Set<string>();
  const out: FieldProblem[] = [];
  const add = (path: string, message: string) => {
    if (seen.has(`${path} ${message}`)) return;
    seen.add(`${path} ${message}`);
    out.push({ path, message });
  };
  for (const e of validator.validate(value).errors) {
    const base = `/input${e.instanceLocation.replace(/^#/, "")}`;
    if (e.keyword === "required") {
      add(`${base}/${quoted(e.error) ?? ""}`.replace(/\/$/, ""), "is required");
    } else if (e.keyword === "additionalProperties") {
      // reported for a declared property whose own value failed too; only an undeclared one counts
      const name = quoted(e.error);
      const parent = at(schema, e.keywordLocation.replace(/\/additionalProperties$/, ""));
      const declared = isObject(parent) && isObject(parent.properties) ? parent.properties : {};
      if (name && !(name in declared)) add(`${base}/${name}`, "is not declared by this Sub-Skill");
    } else if (!SUMMARY.has(e.keyword)) {
      add(base, e.error);
    }
  }
  return out;
}

export const MAX_INLINE_JSON = 2048; // longer JSON is collapsed behind "Show" (E13.7)

/** A value as indented JSON text, and whether it is long enough to collapse. Never markup. */
export function jsonText(value: unknown): { text: string; long: boolean } {
  const text = typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "");
  return { text, long: text.length > MAX_INLINE_JSON };
}
