// A backend document as the workbench's upload row: the scan state, the verdict and whether it was released
// to the knowledge build. The backend owns the state; nothing here invents a verdict.
import type { DocumentOut, DocumentVersionState } from "@/contracts";

import type { ParseInfo, ScanEvent, UploadRecord } from "../api/types";

/** What the FDE reads for each reason the server refuses or rejects a file. */
export const REASONS: Record<string, string> = {
  FILE_TOO_LARGE: "Larger than 15 MB",
  UNSUPPORTED_TYPE: "File type not supported",
  FILE_LIMIT_REACHED: "This project already has 20 files",
  DUPLICATE: "Already in this project",
  FORBIDDEN: "This upload is not allowed for this project",
  PROJECT_ARCHIVED: "This project is archived and read-only",
  NOT_FOUND: "Project not found",
  VALIDATION_ERROR: "This file name or size is not valid",
  NOT_ACCEPTED: "The server did not accept this file",
};

export const reasonFor = (code: string | undefined | null, fallback = "Upload failed"): string =>
  (code && REASONS[code]) || fallback;

/** What the FDE reads for each reason the loader could not read a file. */
export const PARSE_REASONS: Record<string, string> = {
  CORRUPT: "The file is damaged and cannot be read",
  ENCRYPTED: "The file is password protected",
  EMPTY: "The file has no readable content",
  TIMEOUT: "Reading the file took too long",
  PARSE_ERROR: "The file could not be read",
  OCR_UNAVAILABLE: "Scanned pages could not be read: text recognition is not available",
};

/** The loader's verdict for a document that has been released to parsing; undefined before that. */
export function parseInfoOf(d: DocumentOut): ParseInfo | undefined {
  if (d.state !== "PARSING" && d.state !== "PARSED" && d.state !== "PARSE_FAILED") return undefined;
  const r = d.parse_report;
  return {
    state: d.state,
    reason: d.state === "PARSE_FAILED" ? (d.parse_code && PARSE_REASONS[d.parse_code]) || PARSE_REASONS.PARSE_ERROR : undefined,
    pages: r?.pages ?? 0,
    sheets: r?.sheets ?? 0,
    tables: r?.tables ?? 0,
    warnings: r?.warnings ?? [],
    ocrPages: r?.ocr_pages?.length ?? 0,
    unreadablePages: r?.unreadable_pages?.length ?? 0,
  };
}

const RELEASED: ReadonlySet<DocumentVersionState> = new Set(["PARSING", "PARSED", "PARSE_FAILED"]);
const GONE: ReadonlySet<DocumentVersionState> = new Set(["DELETED", "PURGED"]);

/** Documents in the recycle bin are not part of the batch. */
export const isActive = (d: DocumentOut): boolean => !GONE.has(d.state) && !d.deleted_at;

export function toUploadRecord(d: DocumentOut): UploadRecord {
  const released = RELEASED.has(d.state);
  const rejected = d.state === "REJECTED";
  const failedScan = d.state === "SCAN_FAILED";
  return {
    id: d.id,
    batchId: "project",
    name: d.title,
    size: d.size,
    status: "stored",
    uploadedAt: d.created_at,
    objectKey: "", // the storage path is not exposed to the browser
    parts: 1,
    scan: rejected ? "blocked" : released ? "verified" : "scanning",
    threat: rejected ? reasonFor(d.reject_code, "Rejected by the scan") : undefined,
    scanError: failedScan ? "The scan did not finish." : undefined,
    released,
    parse: parseInfoOf(d),
    clamavVerdict: rejected ? "threat" : released ? "clean" : "pending",
  };
}

/** One event per document from its current state (the backend keeps no scan log to read). */
export function scanEventsOf(documents: DocumentOut[]): ScanEvent[] {
  return documents.filter(isActive).map((d) => ({
    id: `${d.id}:${d.state}`,
    fileId: d.id,
    name: d.title,
    kind:
      d.state === "REJECTED"
        ? "threat"
        : d.state === "SCAN_FAILED"
          ? "error"
          : RELEASED.has(d.state)
            ? "clean"
            : d.state === "SCANNING"
              ? "started"
              : "staged",
    at: d.created_at,
  }));
}
