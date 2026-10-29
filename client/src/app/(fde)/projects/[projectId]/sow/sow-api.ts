// SOW screen (E6.5): API paths, rules and copy. Plain data and functions, so they are easy to test.
import type { SowDiff, SowImpact, SowVersionSummary } from "@/contracts";
import { API_BASE, type ApiError } from "@/providers/data-provider";

import { problemCode } from "../../workspace-api";

export const sowPath = (p: string) => `projects/${p}/sow`;
export const versionsPath = (p: string) => `projects/${p}/sow/versions`;
export const versionPath = (p: string, v: number) => `projects/${p}/sow/versions/${v}`;
export const diffPath = (p: string, v: number, other: number) =>
  `projects/${p}/sow/versions/${v}/diff/${other}`;
export const regeneratePath = (p: string) => `projects/${p}/sow/regenerate`;
export const approvePath = (p: string, v: number) => `projects/${p}/sow/versions/${v}/approve`;
export const citationPath = (p: string, v: number, ref: string) =>
  `projects/${p}/sow/versions/${v}/citations/${encodeURIComponent(ref)}`;
/** The browser downloads these itself: the proxy forwards the file (or the 302 to it). */
export const citationSourceUrl = (p: string, v: number, ref: string) =>
  `${API_BASE}/${citationPath(p, v, ref)}/source`;
export const EXPORT_FORMATS = [
  { key: "pdf", label: "PDF" },
  { key: "docx", label: "Word (DOCX)" },
  { key: "md", label: "Markdown" },
] as const;
export type ExportKey = (typeof EXPORT_FORMATS)[number]["key"];
export const exportUrl = (p: string, v: number, format: ExportKey) =>
  `${API_BASE}/projects/${p}/sow/versions/${v}/export?format=${format}`;

export const POLL_MS = 4000; // while a generation runs
export const MAX_TIPTAP_BYTES = 2 * 1024 * 1024; // the API's limit (413 above it)
export const MAX_HINT = 2000;
export const EMPTY_SOW = "The SOW is generated once knowledge is ready.";
export const GENERATING = "Generating the SOW…";
export const FAILED_NO_VERSION =
  "SOW generation failed before a version was written. Regenerate to try again.";
export const SAVED = (version: number) => `Saved as v${version}`;
export const NO_CHANGES = "No changes to save.";
export const APPROVED_BANNER =
  "Approved — this SOW is read-only. A scope change starts a new version.";
export const SUPERSEDED_BANNER = "Superseded — a newer version was approved. Read-only.";
export const SOURCE_DELETED = "Source deleted";

export const isLocked = (status: string | undefined): boolean =>
  status === "APPROVED" || status === "SUPERSEDED";

/** Bytes of the editor document as compact JSON: what the API measures against its 2 MB limit. */
export const tiptapBytes = (doc: unknown): number =>
  new TextEncoder().encode(JSON.stringify(doc)).length;

/** The newest version: the API sorts newest first, but never trust an order you can check. */
export const newest = (items: SowVersionSummary[] | undefined): SowVersionSummary | undefined =>
  [...(items ?? [])].sort((a, b) => b.version - a.version)[0];

export const newKey = (): string => crypto.randomUUID();

/** The sections whose change is not UNCHANGED, with the items that changed. */
export const changedSections = (diff: SowDiff | undefined) =>
  (diff?.sections ?? []).filter((s) => s.change !== "UNCHANGED");

const SAVE_MESSAGES: Record<string, string> = {
  SOW_VERSION_CONFLICT: "Someone saved a newer version while you were editing.",
  SOW_LOCKED: "This SOW is approved and read-only.",
  PAYLOAD_TOO_LARGE: "The SOW is larger than 2 MB. Shorten it before saving.",
  PRECONDITION_REQUIRED: "Reload the SOW before saving.",
  VALIDATION_ERROR: "The SOW does not match the template.",
};

const REGENERATE_MESSAGES: Record<string, string> = {
  SOW_APPROVED: "An approved SOW is not regenerated; start a new version for a scope change.",
  SOW_GENERATING: "A SOW generation is already running.",
  NOT_IN_REVIEW: "There is no SOW to regenerate yet.",
  KNOWLEDGE_NOT_READY: "Knowledge is not ready yet.",
  TEMPORAL_UNAVAILABLE: "The processing engine is unavailable. Try again shortly.",
};

function messageOf(error: unknown, table: Record<string, string>, fallback: string): string {
  const code = problemCode(error);
  const e = error as ApiError | null;
  return (code && table[code]) ?? e?.problem?.detail ?? e?.message ?? fallback;
}

// --- approval (E7.3, the API is E7.1) ---------------------------------------------------------------

export const APPROVABLE = new Set(["GENERATED", "IN_REVIEW"]);
export const SAVE_FIRST = "Save your edits first: approval signs off a saved version.";
export const STALE_TITLE = "A newer version exists";
export const ORCHESTRATOR_DOWN = "Orchestrator unavailable, try again.";

/** Why Approve is off for this version, or null: only the latest, reviewable, hashed version. */
export function approveBlocked(
  version: SowVersionSummary | undefined,
  latest: number,
  generating: boolean,
): string | null {
  if (!version) return "Open a version first";
  if (generating) return "A generation is running";
  if (version.version !== latest) return `Only the latest version (v${latest}) can be approved`;
  if (!APPROVABLE.has(version.status)) return `A ${version.status} version can't be approved`;
  if (!version.sha256) return "This version has no hash to approve";
  return null;
}

/** How the screen answers a failed approval. A stale one never retries with another hash. */
export function approveFailure(error: unknown): "stale" | "unavailable" | "other" {
  const code = problemCode(error);
  if (code === "SOW_STALE" || code === "SOW_NOT_LATEST") return "stale";
  if (statusOf(error) === 503 || code === "ORCHESTRATOR_UNAVAILABLE") return "unavailable";
  return "other";
}

const APPROVE_MESSAGES: Record<string, string> = {
  PIPELINE_NOT_WAITING: "The pipeline is not waiting for an SOW approval right now.",
  ORCHESTRATOR_UNAVAILABLE: ORCHESTRATOR_DOWN,
};
export const approveErrorMessage = (error: unknown) =>
  messageOf(error, APPROVE_MESSAGES, "Could not approve.");

// --- scope change (E7.3, the API is E7.2): new version + impact preview --------------------------------

export const newVersionPath = (p: string) => `projects/${p}/sow/new-version`;
export const impactPath = (p: string, v: number) => `projects/${p}/sow/versions/${v}/impact`;
export const MAX_REASON = 500;
export const NO_REBUILD = "No Sub-Skills need rebuild";

/** What is wrong with a scope-change reason, or null (the API checks 1-500 too). */
export function reasonProblem(reason: string): string | null {
  if (!reason.trim()) return "Say why the scope changes";
  if (reason.length > MAX_REASON) return `At most ${MAX_REASON} characters`;
  return null;
}

/** A draft made from the approved SOW: approving it invalidates what its impact lists. */
export const isScopeChange = (
  approved: SowVersionSummary | null | undefined,
  version: SowVersionSummary | undefined,
): boolean =>
  Boolean(approved && version && version.version > approved.version && !isLocked(version.status));

/** "2 Sub-Skills to rebuild, 1 Macro Skill affected, 1 new module, 14 reused": the API's counts. */
export function impactSummary(impact: SowImpact): string {
  const n = (count: number, one: string, many = `${one}s`) =>
    `${count} ${count === 1 ? one : many}`;
  const parts = [
    impact.affected_sub_skills.length
      ? `${n(impact.affected_sub_skills.length, "Sub-Skill")} to rebuild`
      : NO_REBUILD,
    `${n(impact.affected_macro_skills.length, "Macro Skill")} affected`,
  ];
  if (impact.added_modules.length) parts.push(n(impact.added_modules.length, "new module"));
  parts.push(`${impact.reused_sub_skills_count} reused`);
  return parts.join(", ");
}

const NEW_VERSION_MESSAGES: Record<string, string> = {
  DRAFT_EXISTS: "A new version is already open: edit or approve it.",
  NO_APPROVED_SOW: "Nothing is approved yet: approve a version first.",
};
export const newVersionErrorMessage = (error: unknown) =>
  messageOf(error, NEW_VERSION_MESSAGES, "Could not create a new version.");

export const saveErrorMessage = (error: unknown) =>
  messageOf(error, SAVE_MESSAGES, "Could not save.");
export const regenerateErrorMessage = (error: unknown) =>
  messageOf(error, REGENERATE_MESSAGES, "Could not regenerate.");

/** "scope.in_scope: missing" lines for a 422, so the FDE sees where the template broke. */
export const fieldProblems = (error: unknown): string[] =>
  ((error as ApiError | null)?.problem?.errors ?? []).map((e) => `${e.path}: ${e.message}`);

export const statusOf = (error: unknown): number | undefined =>
  (error as ApiError | null)?.statusCode;

export const where = (location: Record<string, unknown> | undefined): string => {
  const parts = ["page", "line", "sheet", "cell"]
    .filter((k) => location?.[k] !== undefined && location?.[k] !== null)
    .map((k) => `${k} ${String(location?.[k])}`);
  return parts.join(", ") || "whole document";
};
