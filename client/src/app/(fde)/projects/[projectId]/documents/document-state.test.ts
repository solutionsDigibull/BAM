import { describe, expect, test } from "vitest";

import type { DocumentOut, DocumentVersionState } from "@/contracts";

import {
  canRetry,
  countActive,
  daysLeft,
  daysLeftText,
  deleteErrorMessage,
  formatSize,
  hasActions,
  isSettled,
  restoreErrorMessage,
  retryErrorMessage,
  shouldPoll,
  stageChips,
  stateReason,
} from "./document-state";

const doc = (state: DocumentVersionState, over: Partial<DocumentOut> = {}): DocumentOut => ({
  id: `d-${state}`,
  title: "rfq.pdf",
  state,
  size: 100,
  mime: "application/pdf",
  sha256: "x",
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

const text = (state: DocumentVersionState) =>
  stageChips(state).map((c) => `${c.stage}:${c.status}:${c.text}`);
const err = (statusCode: number, code?: string) => ({
  statusCode,
  message: "boom",
  problem: code ? { code } : undefined,
});

describe("stage chips (E4.11)", () => {
  test("a document walks UPLOADED to SCANNING to PARSING to PARSED", () => {
    expect(text("UPLOADED")).toEqual([
      "scan:pending:Waiting",
      "parse:pending:Waiting",
      "ocr:na:n/a",
    ]);
    expect(text("SCANNING")[0]).toBe("scan:running:Scanning");
    expect(text("PARSING")).toEqual(["scan:ok:Clean", "parse:running:Parsing", "ocr:na:n/a"]);
    expect(text("PARSED")).toEqual(["scan:ok:Clean", "parse:ok:Parsed", "ocr:na:n/a"]);
  });

  test("failures name the stage that failed", () => {
    expect(text("SCAN_FAILED")[0]).toBe("scan:failed:Failed");
    expect(text("PARSE_FAILED").slice(0, 2)).toEqual(["scan:ok:Clean", "parse:failed:Failed"]);
  });

  test("e4_11_t06 an infected file is Quarantined, parsing is skipped", () => {
    expect(text("REJECTED").slice(0, 2)).toEqual([
      "scan:failed:Quarantined",
      "parse:skipped:Skipped",
    ]);
    expect(stateReason("REJECTED")).toMatch(/quarantined/);
  });

  test("a failed file says why and what to do", () => {
    expect(stateReason("SCAN_FAILED")).toMatch(/Retry/);
    expect(stateReason("PARSE_FAILED")).toMatch(/Retry/);
    expect(stateReason("PARSED")).toBeNull();
  });
});

describe("actions (E4.11)", () => {
  test("only a failed scan or parse can be retried", () => {
    const states: DocumentVersionState[] = [
      "UPLOADED",
      "SCANNING",
      "PARSING",
      "PARSED",
      "REJECTED",
      "SCAN_FAILED",
      "PARSE_FAILED",
    ];
    expect(states.filter(canRetry)).toEqual(["SCAN_FAILED", "PARSE_FAILED"]);
  });

  test("a quarantined file has no actions", () => {
    expect(hasActions("REJECTED")).toBe(false);
    expect(hasActions("PARSED")).toBe(true);
  });
});

describe("polling (E4.11)", () => {
  test("keeps asking while a document is in flight, however the pipeline is", () => {
    expect(shouldPoll([doc("SCANNING")], "IDLE")).toBe(true);
    expect(shouldPoll([doc("PARSED"), doc("PARSING")], "KNOWLEDGE_BUILDING")).toBe(true);
    expect(shouldPoll([doc("UPLOADED")], undefined)).toBe(true);
  });

  test("keeps asking while the pipeline is ingesting", () => {
    expect(shouldPoll([doc("PARSED")], "INGESTING")).toBe(true);
  });

  test("e4_11_t07 stops once everything settled and the pipeline is not ingesting", () => {
    expect(shouldPoll([doc("PARSED")], "KNOWLEDGE_BUILDING")).toBe(false);
    expect(shouldPoll([doc("PARSED"), doc("SCAN_FAILED"), doc("REJECTED")], "IDLE")).toBe(false);
    expect(shouldPoll([], "IDLE")).toBe(false);
  });

  test("a deleted document that never settled does not keep the poll going", () => {
    expect(shouldPoll([doc("SCANNING", { deleted_at: "2026-10-15T10:00:00Z" })], "IDLE")).toBe(
      false,
    );
  });

  test("settled states", () => {
    expect(isSettled("PARSED") && isSettled("REJECTED") && isSettled("PARSE_FAILED")).toBe(true);
    expect(isSettled("PARSING") || isSettled("UPLOADED")).toBe(false);
  });
});

describe("the 20-file count and the bin", () => {
  test("rejected, deleted and purged documents do not count", () => {
    expect(
      countActive([
        doc("PARSED", { id: "1" }),
        doc("REJECTED", { id: "2" }),
        doc("DELETED", { id: "3" }),
        doc("PURGED", { id: "4" }),
        doc("PARSED", { id: "5", deleted_at: "2026-10-14T10:00:00Z" }),
        doc("SCAN_FAILED", { id: "6" }),
      ]),
    ).toBe(2);
  });

  test("days left until the purge", () => {
    const now = new Date("2026-10-16T12:00:00Z");
    expect(daysLeft("2027-01-14T12:00:00Z", now)).toBe(90);
    expect(daysLeftText("2026-10-17T00:00:00Z", now)).toBe("1 day left");
    expect(daysLeftText("2026-10-26T12:00:00Z", now)).toBe("10 days left");
    expect(daysLeftText("2026-10-15T12:00:00Z", now)).toBe("Purged today");
    expect(daysLeft(null)).toBe(0);
  });

  test("sizes", () => {
    expect(formatSize(100)).toBe("1 KB");
    expect(formatSize(2048)).toBe("2 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("messages for what the API answers (E4.11)", () => {
  test("e4_11_t03 retrying a document that is not failed", () => {
    expect(retryErrorMessage(err(409, "CONFLICT"))).toBe("Only failed documents can be retried");
  });

  test("a document deleted by someone else while the FDE was looking", () => {
    expect(retryErrorMessage(err(404))).toMatch(/no longer exists/);
    expect(deleteErrorMessage(err(404))).toMatch(/already deleted/);
    expect(restoreErrorMessage(err(404))).toMatch(/no longer exists/);
  });

  test("e4_11_t05 restoring after the purge", () => {
    expect(restoreErrorMessage(err(410, "GONE"))).toBe("This document was permanently deleted");
  });

  test("locked, limit and engine-down answers", () => {
    expect(deleteErrorMessage(err(423))).toMatch(/frozen version/);
    expect(retryErrorMessage(err(423))).toMatch(/locked/);
    expect(restoreErrorMessage(err(409, "FILE_LIMIT_REACHED"))).toMatch(/20 files/);
    expect(restoreErrorMessage(err(409, "CONFLICT"))).toMatch(/not in the recycle bin/);
    expect(retryErrorMessage(err(503, "ORCHESTRATOR_UNAVAILABLE"))).toMatch(/unavailable/);
  });

  test("anything else shows the problem detail", () => {
    const e = { statusCode: 500, message: "x", problem: { detail: "Database is down" } };
    expect(retryErrorMessage(e)).toBe("Database is down");
    expect(deleteErrorMessage(e)).toBe("Database is down");
    expect(restoreErrorMessage(e)).toBe("Database is down");
    expect(retryErrorMessage(null)).toBe("Could not retry this document.");
  });
});
