// What the Documents screen shows for each document state (E4.11, TD §9.3): the scan / parse / OCR chips,
// the reason for a failure, which actions are offered, when to keep polling and the messages for the
// errors the API answers. Plain data and functions, so it is easy to test.
//
// The API gives one `state` per document (`DocumentVersionState`), not one per stage, so the chips are
// derived from it. OCR has no state of its own yet (E4.8, #74): its chip reads "n/a".
import type { DocumentOut, DocumentVersionState, ProjectPipelineState } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const POLL_MS = 4000;
const DAY_MS = 24 * 60 * 60 * 1000;

export type Stage = "scan" | "parse" | "ocr";
export type ChipStatus = "pending" | "running" | "ok" | "failed" | "skipped" | "na";
export type Chip = { stage: Stage; status: ChipStatus; text: string };

const chip = (stage: Stage, status: ChipStatus, text: string): Chip => ({ stage, status, text });
const OCR = chip("ocr", "na", "n/a");

export function stageChips(state: DocumentVersionState): Chip[] {
  switch (state) {
    case "UPLOADED":
      return [chip("scan", "pending", "Waiting"), chip("parse", "pending", "Waiting"), OCR];
    case "SCANNING":
      return [chip("scan", "running", "Scanning"), chip("parse", "pending", "Waiting"), OCR];
    case "REJECTED":
      // ponytail: only the malware scan rejects today. When #70 rejects on type or content, the API
      // needs a reason field, and this chip should say "Rejected" for those.
      return [chip("scan", "failed", "Quarantined"), chip("parse", "skipped", "Skipped"), OCR];
    case "SCAN_FAILED":
      return [chip("scan", "failed", "Failed"), chip("parse", "pending", "Waiting"), OCR];
    case "PARSING":
      return [chip("scan", "ok", "Clean"), chip("parse", "running", "Parsing"), OCR];
    case "PARSED":
      return [chip("scan", "ok", "Clean"), chip("parse", "ok", "Parsed"), OCR];
    case "PARSE_FAILED":
      return [chip("scan", "ok", "Clean"), chip("parse", "failed", "Failed"), OCR];
    default: // DELETED, PURGED: only the recycle bin lists them
      return [chip("scan", "na", "n/a"), chip("parse", "na", "n/a"), OCR];
  }
}

const REASONS: Partial<Record<DocumentVersionState, string>> = {
  REJECTED:
    "The malware scan flagged this file. It is quarantined: it cannot be downloaded or retried.",
  SCAN_FAILED: "The scan could not finish (the scanner was unavailable). Retry it.",
  PARSE_FAILED: "The file could not be read. Retry it, or upload a corrected copy.",
};

/** One line saying why a document failed, or null. */
export const stateReason = (state: DocumentVersionState): string | null => REASONS[state] ?? null;

const TERMINAL: readonly DocumentVersionState[] = [
  "PARSED",
  "REJECTED",
  "SCAN_FAILED",
  "PARSE_FAILED",
  "DELETED",
  "PURGED",
];
const INACTIVE: readonly DocumentVersionState[] = ["REJECTED", "DELETED", "PURGED"];
const RETRYABLE: readonly DocumentVersionState[] = ["SCAN_FAILED", "PARSE_FAILED"];

export const isSettled = (state: DocumentVersionState): boolean => TERMINAL.includes(state);
/** Only a failed scan or parse can be retried; the API answers 409 for anything else. */
export const canRetry = (state: DocumentVersionState): boolean => RETRYABLE.includes(state);
/** A quarantined (infected) file has no actions at all. */
export const hasActions = (state: DocumentVersionState): boolean => state !== "REJECTED";

/** Documents that count toward the project's 20-file limit. */
export const countActive = (docs: DocumentOut[]): number =>
  docs.filter((d) => !d.deleted_at && !INACTIVE.includes(d.state)).length;

export const inBin = (docs: DocumentOut[]): DocumentOut[] => docs.filter((d) => d.deleted_at);
export const inList = (docs: DocumentOut[]): DocumentOut[] => docs.filter((d) => !d.deleted_at);

/** Keep asking while the pipeline is ingesting or any listed document has not settled. */
export function shouldPoll(docs: DocumentOut[], pipeline: ProjectPipelineState | undefined) {
  return pipeline === "INGESTING" || inList(docs).some((d) => !isSettled(d.state));
}

/** Whole days until the purge, rounded up, never below 0. */
export function daysLeft(purgeAfter: string | null | undefined, now: Date = new Date()): number {
  if (!purgeAfter) return 0;
  return Math.max(0, Math.ceil((new Date(purgeAfter).getTime() - now.getTime()) / DAY_MS));
}

export function daysLeftText(purgeAfter: string | null | undefined, now?: Date): string {
  const days = daysLeft(purgeAfter, now);
  return days === 0 ? "Purged today" : days === 1 ? "1 day left" : `${days} days left`;
}

export function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// --- messages for what the API answers ---------------------------------------------------------

const code = (error: unknown) => (error as ApiError | null)?.problem?.code;
const status = (error: unknown) => (error as ApiError | null)?.statusCode;
const detail = (error: unknown, fallback: string) =>
  (error as ApiError | null)?.problem?.detail ?? (error as ApiError | null)?.message ?? fallback;

const GONE = "This document no longer exists. It may have been deleted by someone else.";
const LOCKED = "This document is used by a frozen version and is locked.";

export function retryErrorMessage(error: unknown): string {
  if (status(error) === 409) return "Only failed documents can be retried";
  if (status(error) === 404) return GONE;
  if (status(error) === 423) return LOCKED;
  if (code(error) === "ORCHESTRATOR_UNAVAILABLE")
    return "The processing engine is unavailable. Try again shortly.";
  return detail(error, "Could not retry this document.");
}

export function deleteErrorMessage(error: unknown): string {
  if (status(error) === 423)
    return "This document is used by a frozen version and cannot be deleted.";
  if (status(error) === 404) return "This document was already deleted.";
  return detail(error, "Could not delete this document.");
}

export function restoreErrorMessage(error: unknown): string {
  if (status(error) === 410) return "This document was permanently deleted";
  if (code(error) === "FILE_LIMIT_REACHED")
    return "The project already has 20 files. Delete one before restoring this document.";
  if (status(error) === 409) return "This document is not in the recycle bin.";
  if (status(error) === 404) return GONE;
  if (status(error) === 423) return LOCKED;
  return detail(error, "Could not restore this document.");
}
