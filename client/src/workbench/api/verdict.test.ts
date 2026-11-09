import { describe, expect, test } from "vitest";

import type { ParseInfo, UploadRecord } from "./types";
import { fileVerdict } from "./verdict";

const parse = (over: Partial<ParseInfo> = {}): ParseInfo => ({ state: "PARSED", pages: 0, sheets: 0, tables: 0, warnings: [], ocrPages: 0, unreadablePages: 0, ...over });
const file = (p?: ParseInfo): UploadRecord => ({ id: "u", name: "a.pdf", parse: p }) as UploadRecord;

describe("the verdict the FDE reads for a file comes from the loader, not the browser (#711)", () => {
  test("test_711_a_file_read_without_notes_is_ready_with_its_facts", () => {
    const v = fileVerdict(file(parse({ pages: 12, tables: 1, ocrPages: 2 })));
    expect(v.state).toBe("ready");
    expect(v.facts).toEqual(["12 pages", "1 table", "2 pages read by text recognition"]);
  });

  test("test_711_unreadable_pages_or_warnings_need_attention", () => {
    expect(fileVerdict(file(parse({ unreadablePages: 1 }))).reason).toBe("1 page could not be read");
    expect(fileVerdict(file(parse({ unreadablePages: 3 }))).state).toBe("attention");
    const w = fileVerdict(file(parse({ warnings: ["Merged cells", "Hidden sheet"] })));
    expect(w.state).toBe("attention");
    expect(w.reason).toBe("Merged cells; Hidden sheet");
  });

  test("test_711_a_file_the_loader_could_not_read_fails_with_the_reason", () => {
    const v = fileVerdict(file(parse({ state: "PARSE_FAILED", reason: "The file is password protected" })));
    expect(v.state).toBe("failed");
    expect(v.reason).toBe("The file is password protected");
  });

  test("test_711_a_file_still_being_read_or_without_a_verdict_is_checking", () => {
    expect(fileVerdict(file(parse({ state: "PARSING" }))).state).toBe("checking");
    expect(fileVerdict(file(undefined)).state).toBe("checking");
  });
});
