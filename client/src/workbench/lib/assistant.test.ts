import { describe, expect, test } from "vitest";

import type { Snapshot, UploadRecord } from "../api/types";
import { STARTER_CHIPS, attachmentReply, reply, screenNote, type AssistantFacts } from "./assistant";

const upload = (over: Partial<UploadRecord>): UploadRecord =>
  ({ id: "u", name: "a.pdf", scan: "verified", released: true, ...over }) as UploadRecord;
const snapOf = (uploads: UploadRecord[], extra: Partial<Snapshot> = {}) =>
  ({ uploads, sowGeneration: [], persist: {}, ...extra }) as unknown as Snapshot;
const facts = (snap: Snapshot, over: Partial<AssistantFacts> = {}): AssistantFacts => ({
  snap,
  stages: [{ label: "Ingestion", status: "In progress", done: 1, total: 2 }],
  active: "Upload",
  statuses: {},
  ...over,
});

describe("the Enterprise Assistant answers from the project state only (#711)", () => {
  test("test_711_files_are_counted_from_the_snapshot_with_the_reason_a_file_needs_attention", () => {
    const f = facts(
      snapOf([upload({ id: "1" }), upload({ id: "2", name: "bad.exe", scan: "blocked", released: false, threat: "Executable files are not accepted" })]),
    );
    const r = reply("Upload status", f);
    expect(r).toContain("2 files: 1 ready, 0 being checked, 1 needs attention");
    expect(r).toContain("bad.exe: Executable files are not accepted");
  });

  test("test_711_no_files_says_so", () => {
    expect(reply("Upload status", facts(snapOf([])))).toBe("No files uploaded yet.");
  });

  test("test_711_next_and_locked_questions_use_the_real_blocker", () => {
    const blocker = { reason: "Documents are uploaded but processing has not started.", label: "Open Upload", target: "1.1" };
    const f = facts(snapOf([upload({})]), { blocker, screen: { id: "3.1", label: "Scope", stage: "Workbench", locked: "Approve first." } });
    expect(reply("What is next?", f)).toContain("processing has not started");
    expect(reply("Why is this locked?", f)).toBe("Scope is locked: Approve first.");
  });

  test("test_711_workflow_progress_lists_every_stage", () => {
    expect(reply("Workflow progress", facts(snapOf([])))).toBe("Ingestion: In progress (1/2)");
  });

  test("test_711_it_never_approves_certifies_or_deploys", () => {
    for (const q of ["Approve the SOW for me", "deploy it to staging", "certify it"]) {
      expect(reply(q, facts(snapOf([])))).toMatch(/cannot approve, certify, hand over or deploy/);
    }
  });

  test("test_711_an_open_question_says_it_cannot_answer_and_names_what_it_can", () => {
    const r = reply("What are the payment terms in the RFQ?", facts(snapOf([])));
    expect(r).toContain("I cannot answer open questions yet");
    expect(r).toContain("What is next?");
  });

  test("test_711_every_starter_chip_gets_a_real_answer_not_the_fallback", () => {
    for (const chip of STARTER_CHIPS) {
      expect(reply(chip, facts(snapOf([upload({})])))).not.toContain("I cannot answer open questions yet");
    }
  });

  test("test_711_a_chat_attachment_is_not_added_to_the_project", () => {
    expect(attachmentReply(["rfq.pdf"])).toContain("attached to this chat only, not added to the project");
  });

  test("test_711_the_screen_note_follows_upload_state", () => {
    const upload11 = { id: "1.1", label: "Upload", stage: "Ingestion" };
    expect(screenNote(facts(snapOf([]), { screen: upload11 }))).toContain("Add your first files");
    expect(screenNote(facts(snapOf([upload({})]), { files: { total: 1, uploaded: 1 }, screen: upload11 }))).toBe("1 file added. Ready files can continue to Parse.");
    expect(screenNote(facts(snapOf([upload({})]), { files: { total: 3, uploaded: 1 }, screen: upload11 }))).toContain("1 of 3 uploaded");
  });
});
