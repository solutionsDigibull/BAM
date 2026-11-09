import { describe, expect, test } from "vitest";

import type { ProjectPipelineState } from "@/contracts";

import { actionLabel, stateOf, workbenchHref } from "./project-state";

const ALL: ProjectPipelineState[] = [
  "IDLE",
  "INGESTING",
  "KNOWLEDGE_BUILDING",
  "SOW_GENERATING",
  "SOW_REVIEW",
  "SOW_APPROVED",
  "WORKFLOW_CREATED",
  "INGESTION_FAILED",
  "KNOWLEDGE_FAILED",
  "SOW_GENERATION_FAILED",
  "CANCELLED",
];

describe("what a project's state says on the landing page (#719)", () => {
  test("test_719_every_pipeline_state_has_a_plain_label", () => {
    for (const s of ALL) expect(stateOf(s).label).not.toBe(s);
  });

  test("test_719_every_failure_says_needs_attention", () => {
    for (const s of ["INGESTION_FAILED", "KNOWLEDGE_FAILED", "SOW_GENERATION_FAILED"] as const) {
      expect(stateOf(s)).toEqual({ label: "Needs attention", tone: "failed" });
    }
  });

  test("test_719_only_a_project_nobody_started_says_start", () => {
    expect(actionLabel("IDLE")).toBe("Start");
    for (const s of ALL.filter((x) => x !== "IDLE")) expect(actionLabel(s)).toBe("Resume");
  });

  test("test_719_the_link_is_the_same_for_every_state_because_the_workbench_resumes_itself", () => {
    expect(workbenchHref("abc")).toBe("/projects/abc/workbench");
  });
});
