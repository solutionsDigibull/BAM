import { describe, expect, test } from "vitest";

import type { StagingStatus } from "@/contracts";

import { canShowUi, isNotAvailable, isTerminal, shortDigest, stagingPollMs, STAGING_RUN_POLL_MS, STAGING_WAIT_POLL_MS, stagingStateTone } from "./staging";

const status = (over: Partial<StagingStatus> = {}): StagingStatus => ({ workflow_id: "w1", state: "RUNNING", health: "HEALTHY", ui_url: "/ui/", ...over });

describe("Staging rules (#861)", () => {
  test("test_861_polling_is_quick_while_waiting_slow_while_running_and_stops_on_terminal_states", () => {
    expect(stagingPollMs(undefined)).toBe(false);
    expect(stagingPollMs(status({ state: "PENDING_RUNTIME" }))).toBe(STAGING_WAIT_POLL_MS);
    expect(stagingPollMs(status())).toBe(STAGING_RUN_POLL_MS);
    for (const state of ["FAILED", "STOPPED", "SUPERSEDED", "NOT_DEPLOYED"] as const) expect(stagingPollMs(status({ state }))).toBe(false);
    expect(["FAILED", "STOPPED", "SUPERSEDED"].every((s) => isTerminal(s as never))).toBe(true);
    expect(isTerminal("RUNNING")).toBe(false);
  });

  test("test_861_the_ui_shows_only_for_a_running_deployment_with_a_ui_address", () => {
    expect(canShowUi(status())).toBe(true);
    expect(canShowUi(status({ ui_url: null }))).toBe(false);
    expect(canShowUi(status({ state: "PENDING_RUNTIME" }))).toBe(false);
    expect(canShowUi(status({ state: "SUPERSEDED" }))).toBe(false);
    expect(canShowUi(undefined)).toBe(false);
  });

  test("test_861_tones_digests_and_not_found", () => {
    expect(stagingStateTone("RUNNING")).toBe("ok");
    expect(stagingStateTone("FAILED")).toBe("err");
    expect(stagingStateTone("PENDING_RUNTIME")).toBe("warn");
    expect(shortDigest("sha256:" + "ab".repeat(32))).toBe("sha256:abababababab");
    expect(shortDigest(null)).toBe("—");
    expect(isNotAvailable({ statusCode: 404 })).toBe(true);
    expect(isNotAvailable({ statusCode: 403 })).toBe(false);
  });
});
