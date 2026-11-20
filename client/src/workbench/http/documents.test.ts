import { describe, expect, test } from "vitest";

import type { DocumentOut, DocumentVersionState } from "@/contracts";

import { ApiFailure } from "./http";
import { accessReason, deletedOf, isActive, openBlock, restoreReason, scanEventsOf, toUploadRecord } from "./documents";

const doc = (state: DocumentVersionState, extra: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "d1",
  title: "spec.pdf",
  state,
  size: 100,
  mime: "application/pdf",
  sha256: "x",
  created_at: "2026-11-09T10:00:00Z",
  ...extra,
});

describe("a backend document as a workbench upload row (#711)", () => {
  test("test_711_a_clean_parsed_file_is_verified_and_released", () => {
    for (const state of ["PARSING", "PARSED", "PARSE_FAILED"] as const) {
      const r = toUploadRecord(doc(state));
      expect(r.scan).toBe("verified");
      expect(r.released).toBe(true);
      expect(r.clamavVerdict).toBe("clean");
    }
  });

  test("test_711_an_unscanned_file_is_scanning_and_never_released", () => {
    for (const state of ["UPLOADED", "SCANNING"] as const) {
      const r = toUploadRecord(doc(state));
      expect(r.scan).toBe("scanning");
      expect(r.released).toBe(false);
      expect(r.clamavVerdict).toBe("pending");
    }
  });

  test("test_711_a_rejected_file_is_blocked_with_the_servers_reason", () => {
    const r = toUploadRecord(doc("REJECTED", { reject_code: "FILE_TOO_LARGE" }));
    expect(r.scan).toBe("blocked");
    expect(r.threat).toBe("Larger than 15 MB");
    expect(r.released).toBe(false);
  });

  test("test_711_a_scan_that_did_not_finish_is_an_error_not_a_verdict", () => {
    const r = toUploadRecord(doc("SCAN_FAILED"));
    expect(r.scanError).toBeTruthy();
    expect(r.released).toBe(false);
    expect(r.scan).not.toBe("verified");
  });

  test("test_711_recycle_bin_documents_are_not_part_of_the_batch", () => {
    expect(isActive(doc("DELETED"))).toBe(false);
    expect(isActive(doc("PARSED", { deleted_at: "2026-11-09T11:00:00Z" }))).toBe(false);
    expect(isActive(doc("PARSED"))).toBe(true);
  });

  test("test_711_scan_events_follow_the_current_state_of_each_active_document", () => {
    const events = scanEventsOf([
      doc("REJECTED"),
      doc("SCAN_FAILED", { id: "d2" }),
      doc("DELETED", { id: "d3" }),
    ]);
    expect(events.map((e) => e.kind)).toEqual(["threat", "error"]);
  });

  test("test_711_a_parsed_file_carries_the_loaders_findings", () => {
    const r = toUploadRecord(
      doc("PARSED", {
        parse_report: { pages: 12, sheets: 0, tables: 3, warnings: ["Page 4 has no text layer"], ocr_needed_pages: [4, 5], ocr_pages: [4], unreadable_pages: [5], attachments: [] },
      }),
    );
    expect(r.parse).toEqual({
      state: "PARSED",
      reason: undefined,
      pages: 12,
      sheets: 0,
      tables: 3,
      warnings: ["Page 4 has no text layer"],
      ocrPages: 1,
      unreadablePages: 1,
    });
  });

  test("test_711_a_file_the_loader_could_not_read_says_why_in_words", () => {
    expect(toUploadRecord(doc("PARSE_FAILED", { parse_code: "ENCRYPTED" })).parse?.reason).toBe("The file is password protected");
    expect(toUploadRecord(doc("PARSE_FAILED", { parse_code: "CORRUPT" })).parse?.reason).toBe("The file is damaged and cannot be read");
    expect(toUploadRecord(doc("PARSE_FAILED")).parse?.reason).toBe("The file could not be read");
  });

  test("test_711_a_file_not_yet_released_has_no_parse_verdict", () => {
    for (const state of ["UPLOADED", "SCANNING", "REJECTED", "SCAN_FAILED"] as const) {
      expect(toUploadRecord(doc(state)).parse).toBeUndefined();
    }
  });
});

describe("a document's relevance to the goal reaches its upload row (#802)", () => {
  test("test_802_t46_relevance_is_carried_when_scored_and_absent_otherwise", () => {
    const relevance = {
      level: "LOW" as const,
      score: 10,
      reason: "A holiday calendar; nothing about the goal.",
      goal_sha256: "b".repeat(64),
      scored_at: "2026-11-17T09:00:00Z",
    };
    expect(toUploadRecord(doc("PARSED", { relevance })).relevance).toEqual(relevance);
    expect(toUploadRecord(doc("PARSED")).relevance).toBeUndefined();
  });
});

describe("the recycle bin and the reason a restore is refused (#712)", () => {
  test("test_712_the_bin_lists_deleted_files_newest_first_and_never_a_purged_one", () => {
    const bin = deletedOf([
      doc("PARSED"),
      doc("DELETED", { id: "a", title: "old.pdf", deleted_at: "2026-11-01T10:00:00Z", purge_after: "2027-01-30T10:00:00Z" }),
      doc("DELETED", { id: "b", title: "new.pdf", deleted_at: "2026-11-05T10:00:00Z" }),
      doc("PURGED", { id: "c", deleted_at: "2026-08-01T10:00:00Z" }),
    ]);
    expect(bin.map((d) => d.name)).toEqual(["new.pdf", "old.pdf"]);
    expect(bin[1]!.purgeAfter).toBe("2027-01-30T10:00:00Z");
  });

  test("test_712_restore_refusals_are_said_in_words", () => {
    const f = (status: number, code?: string, detail?: string) =>
      new ApiFailure("x", status, { code, detail } as never);
    expect(restoreReason(f(410))).toMatch(/permanently deleted/);
    expect(restoreReason(f(409, "FILE_LIMIT_REACHED"))).toMatch(/20 files/);
    expect(restoreReason(f(409, "CONFLICT"))).toMatch(/not in the recycle bin/);
    expect(restoreReason(f(403))).toMatch(/not allowed/);
    expect(restoreReason(f(500, undefined, "Server said no"))).toBe("Server said no");
    expect(restoreReason(new Error("offline"))).toBe("offline");
  });
});

describe("opening an uploaded file (#895)", () => {
  test("test_895_only_a_released_clean_file_can_be_opened", () => {
    expect(openBlock(toUploadRecord(doc("PARSED")))).toBeUndefined();
    expect(openBlock(toUploadRecord(doc("SCANNING")))).toMatch(/still being checked/i);
    expect(openBlock(toUploadRecord(doc("REJECTED")))).toMatch(/quarantined/);
    expect(openBlock(toUploadRecord(doc("SCAN_FAILED")))).toMatch(/did not finish/);
  });

  test("test_895_a_refused_link_says_why", () => {
    const f = (statusCode: number, detail?: string) => new ApiFailure("m", statusCode, detail ? { detail } : undefined);
    expect(accessReason(f(409, "In the recycle bin"))).toBe("In the recycle bin");
    expect(accessReason(f(409))).toMatch(/cannot be opened/);
    expect(accessReason(f(410))).toMatch(/permanently deleted/);
    expect(accessReason(f(404))).toMatch(/no longer exists/);
    expect(accessReason(f(403))).toMatch(/not allowed/);
    expect(accessReason(new Error("offline"))).toBe("offline");
  });
});
