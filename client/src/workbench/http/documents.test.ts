import { describe, expect, test } from "vitest";

import type { DocumentOut, DocumentVersionState } from "@/contracts";

import { isActive, scanEventsOf, toUploadRecord } from "./documents";

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
});
