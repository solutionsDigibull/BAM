import { describe, expect, test } from "vitest";

import type { PipelineTimeline, ProjectPipelineState } from "@/contracts";

import {
  activeDocuments,
  canStart,
  isRunning,
  startErrorMessage,
  timelineView,
  uploadsPath,
} from "./workspace-api";

const timeline = (
  state: ProjectPipelineState,
  counts: Partial<PipelineTimeline["counts"]> = {},
  steps: PipelineTimeline["steps"] = [],
): PipelineTimeline => ({
  state,
  steps,
  counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0, ...counts },
});

describe("Start processing rules (E4.10)", () => {
  test("enabled with an uploaded document in a fresh project", () => {
    expect(canStart(timeline("IDLE", { uploaded: 1 }))).toBe(true);
  });

  test("disabled with no documents, or only rejected ones", () => {
    expect(canStart(timeline("IDLE"))).toBe(false);
    expect(canStart(timeline("IDLE", { rejected: 3 }))).toBe(false);
  });

  test("a retry after a failed ingestion needs a document too", () => {
    expect(canStart(timeline("INGESTION_FAILED", { failed: 1 }))).toBe(true);
    expect(canStart(timeline("INGESTION_FAILED"))).toBe(false);
  });

  test.each([
    "INGESTING",
    "KNOWLEDGE_BUILDING",
    "SOW_GENERATING",
    "SOW_REVIEW",
    "CANCELLED",
  ] as const)("disabled while the project is %s", (state) => {
    expect(canStart(timeline(state, { uploaded: 2 }))).toBe(false);
  });

  test("scanned and parsed documents count; rejected ones do not", () => {
    expect(activeDocuments({ uploaded: 1, parsed: 2, rejected: 5, failed: 1 })).toBe(4);
  });

  test("only the three working states keep the overview refreshing", () => {
    expect(isRunning("INGESTING")).toBe(true);
    expect(isRunning("IDLE")).toBe(false);
    expect(isRunning("SOW_REVIEW")).toBe(false);
  });
});

describe("Start error messages", () => {
  const err = (code: string, detail = "server detail") => ({
    statusCode: 409,
    message: "m",
    problem: { code, detail },
  });

  test("409 PIPELINE_RUNNING says 'already running'", () => {
    expect(startErrorMessage(err("PIPELINE_RUNNING"))).toBe("Processing is already running.");
  });

  test("known codes get their own wording, unknown ones show the server detail", () => {
    expect(startErrorMessage(err("NO_DOCUMENTS"))).toMatch(/at least one document/);
    expect(startErrorMessage(err("SOMETHING_NEW", "Plain detail"))).toBe("Plain detail");
    expect(startErrorMessage({ statusCode: 500, message: "boom" })).toBe("boom");
    expect(startErrorMessage(null)).toBe("Could not start.");
  });
});

describe("Timeline", () => {
  test("a fresh project sits at Upload, a failed ingestion is an error on Upload", () => {
    expect(timelineView(timeline("IDLE"))).toEqual({ current: 0, failed: false });
    expect(timelineView(timeline("INGESTION_FAILED"))).toEqual({ current: 0, failed: true });
  });

  test("each working state maps to its step", () => {
    expect(timelineView(timeline("KNOWLEDGE_BUILDING")).current).toBe(1);
    expect(timelineView(timeline("SOW_GENERATING")).current).toBe(2);
    expect(timelineView(timeline("SOW_REVIEW")).current).toBe(3);
    expect(timelineView(timeline("SOW_APPROVED")).current).toBe(4);
  });

  test("a cancelled run shows the step it stopped at, without an error", () => {
    const steps: PipelineTimeline["steps"] = [
      { name: "INGESTING", status: "done", started_at: null, ended_at: null },
      { name: "KNOWLEDGE_BUILDING", status: "cancelled", started_at: null, ended_at: null },
    ];
    expect(timelineView(timeline("CANCELLED", {}, steps))).toEqual({ current: 1, failed: false });
  });
});

test("paths go through the project id", () => {
  expect(uploadsPath("abc")).toBe("projects/abc/uploads");
});
