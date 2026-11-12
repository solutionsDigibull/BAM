import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

import type { Snapshot, StepInfo } from "../api/types";

import { backendBusy, SNAPSHOT_IDLE_POLL_MS, SNAPSHOT_POLL_MS, snapshotPollMs } from "./data";

const step = (over: Partial<StepInfo> = {}): StepInfo => ({
  id: 1, title: "", state: "done", reason: "", stale: false, editable: true, background: 0, ...over,
});
const snap = (over: Partial<Snapshot> = {}): Snapshot =>
  ({ steps: [step()], sowGeneration: [], extraction: [], ...over }) as Snapshot;
const stage = (status: "queued" | "running" | "succeeded" | "failed") => ({ key: "k", label: "l", status });

const source = () => readFileSync(join(process.cwd(), "src", "workbench", "lib", "data.ts"), "utf8");

describe("the snapshot poll does not flood the API (#711)", () => {
  test("test_711_the_snapshot_is_polled_no_faster_than_every_three_seconds", () => {
    expect(SNAPSHOT_POLL_MS).toBeGreaterThanOrEqual(3000);
  });

  test("test_711_useSnapshot_uses_that_interval_not_a_literal", () => {
    expect(source()).toContain("snapshotPollMs(");
    expect(source()).not.toMatch(/refetchInterval:\s*\d/);
  });
});

describe("the snapshot poll follows the work (#744)", () => {
  test("test_744_an_idle_project_polls_slowly", () => {
    expect(backendBusy(snap())).toBe(false);
    expect(snapshotPollMs(snap())).toBe(SNAPSHOT_IDLE_POLL_MS);
    expect(SNAPSHOT_IDLE_POLL_MS).toBeGreaterThan(SNAPSHOT_POLL_MS);
  });

  test("test_744_background_work_polls_fast", () => {
    expect(snapshotPollMs(snap({ steps: [step({ background: 2 })] }))).toBe(SNAPSHOT_POLL_MS);
  });

  test("test_744_an_active_step_the_user_cannot_edit_polls_fast", () => {
    expect(snapshotPollMs(snap({ steps: [step({ state: "active", editable: false })] }))).toBe(SNAPSHOT_POLL_MS);
  });

  test("test_744_an_active_step_waiting_for_the_user_polls_slowly", () => {
    expect(snapshotPollMs(snap({ steps: [step({ state: "active", editable: true })] }))).toBe(SNAPSHOT_IDLE_POLL_MS);
  });

  test("test_744_a_queued_or_running_stage_polls_fast_a_finished_one_does_not", () => {
    expect(snapshotPollMs(snap({ sowGeneration: [stage("running")] }))).toBe(SNAPSHOT_POLL_MS);
    expect(snapshotPollMs(snap({ extraction: [stage("queued")] }))).toBe(SNAPSHOT_POLL_MS);
    expect(snapshotPollMs(snap({ sowGeneration: [stage("succeeded"), stage("failed")] }))).toBe(SNAPSHOT_IDLE_POLL_MS);
  });

  test("test_744_no_data_yet_polls_fast", () => {
    expect(snapshotPollMs(undefined)).toBe(SNAPSHOT_POLL_MS);
  });

  test("test_744_a_hidden_tab_does_not_poll", () => {
    expect(source()).not.toContain("refetchIntervalInBackground");
  });
});
