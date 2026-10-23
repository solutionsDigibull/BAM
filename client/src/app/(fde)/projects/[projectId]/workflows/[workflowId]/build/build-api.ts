// The Build & Test dashboard's data rules (TD §9.3, E12.6), free of AG Grid so they can be tested alone.
// GET W/builds and GET W/sub-skills/{id}/gates come with the gate issues (#117 / #119); until they land
// the shapes are sbam_contracts.build_dashboard (#312). Retry is #107's POST W/sub-skills/{id}/retry.
import type { BuildRow, BuildsResponse, GateName, GateStatus, SubSkillState } from "@/contracts";

const workflowUrl = (p: string, w: string) => `projects/${p}/workflows/${w}`;
export const buildsUrl = (p: string, w: string) => `${workflowUrl(p, w)}/builds`;
export const gatesUrl = (p: string, w: string, subId: string) =>
  `${workflowUrl(p, w)}/sub-skills/${subId}/gates`;
export const retryUrl = (p: string, w: string, subId: string) =>
  `${workflowUrl(p, w)}/sub-skills/${subId}/retry`;

/** Where a finding opens in the Code Viewer (read-only, E13). */
export const codeHref = (
  p: string,
  w: string,
  at: { subId: string; attempt: number; file: string; line?: number | null },
) => {
  const q = new URLSearchParams({ sub: at.subId, attempt: String(at.attempt), file: at.file });
  if (at.line) q.set("line", String(at.line));
  return `/projects/${p}/workflows/${w}/code?${q}`;
};

export type GateFamily = "Contract" | "AST" | "Static" | "Tests";

/** The matrix columns, in the order the contract lists them, with the labels TD §9.3 uses. */
export const GATES: readonly { name: GateName; label: string; family: GateFamily }[] = [
  { name: "ISATVON_CONTRACT", label: "Contract", family: "Contract" },
  { name: "LIBRARIES", label: "Libraries", family: "Contract" },
  { name: "SCHEMA_SQL", label: "Schema SQL", family: "Contract" },
  { name: "CODEGEN", label: "Codegen", family: "Contract" },
  { name: "AST_SYNTAX", label: "Syntax", family: "AST" },
  { name: "AST_SKELETON", label: "Skeleton", family: "AST" },
  { name: "AST_CONFORMANCE", label: "Conformance", family: "AST" },
  { name: "AST_DECOUPLING", label: "Decoupling", family: "AST" },
  { name: "AST_NO_MANUAL_LOOP", label: "No loop", family: "AST" },
  { name: "AST_HANDOFF", label: "Handoff", family: "AST" },
  { name: "AST_IMPORTS", label: "Imports", family: "AST" },
  { name: "AST_SQL_SAFETY", label: "SQL safety", family: "AST" },
  { name: "RUFF", label: "Ruff", family: "Static" },
  { name: "BLACK", label: "Black", family: "Static" },
  { name: "MYPY", label: "MyPy", family: "Static" },
  { name: "CLAMAV", label: "ClamAV", family: "Static" },
  { name: "BANDIT", label: "Bandit", family: "Static" },
  { name: "IMPORT_RESOLUTION", label: "Imports", family: "Static" },
  { name: "LOOP_TESTS", label: "Loop tests", family: "Tests" },
];
export const GATE_LABEL = Object.fromEntries(GATES.map((g) => [g.name, g.label])) as Record<
  GateName,
  string
>;

export const MAX_ATTEMPT = 2; // sbam_contracts.build.MAX_CORRECTIONS: attempts 0, 1, 2

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** The usable rows of a builds answer; null, {} or a row without an id or name is dropped. */
export const buildRows = (builds: unknown): BuildRow[] =>
  isObject(builds) && Array.isArray(builds.sub_skills)
    ? (builds.sub_skills as unknown[]).filter(
        (r): r is BuildRow =>
          isObject(r) && typeof r.sub_skill_id === "string" && typeof r.skill_id === "string",
      )
    : [];

/** A gate missing from the row hasn't been reached: PENDING. */
export const gateStatus = (row: BuildRow, gate: GateName): GateStatus =>
  row.gates?.[gate] ?? "PENDING";

/** Retry is offered only to a Sub-Skill waiting for the FDE (TD §13.2); the server still answers 409. */
export const canRetry = (state: SubSkillState | undefined) => state === "NEEDS_FDE_REVIEW";

export const POLL_MS = 3_000;
const MAX_BACKOFF_MS = 30_000;

/**
 * How long until the next GET W/builds: every 3 s while anything is building; never once nothing is
 * active (E12.6-T07). After failures (a dropped network, 429) it backs off: 6, 12, 24, then 30 s.
 */
export function pollMs(builds: BuildsResponse | undefined, failures: number): number | false {
  if (failures > 0) return Math.min(POLL_MS * 2 ** failures, MAX_BACKOFF_MS);
  return builds?.active ? POLL_MS : false;
}

/** The counts the summary line shows. */
export function summary(rows: BuildRow[]) {
  const failed = rows.filter((r) => GATES.some((g) => gateStatus(r, g.name) === "FAILED")).length;
  const review = rows.filter((r) => r.state === "NEEDS_FDE_REVIEW").length;
  return { total: rows.length, failed, review };
}
