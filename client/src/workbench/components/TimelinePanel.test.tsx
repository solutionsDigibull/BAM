import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test } from "vitest";

import type { BuildTimelineResponse } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { formatDuration, idleMs } from "../lib/buildTimeline";
import { TimelinePanel } from "./TimelinePanel";

const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const TIMELINE = (over: Partial<BuildTimelineResponse> = {}): BuildTimelineResponse => ({
  workflow_id: "w1",
  workflow_state: "BUILDING",
  cost_usd: "0.0300",
  sub_skills: [
    {
      sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "NEEDS_FDE_REVIEW", build_id: `build-${SUB}-1`, build_no: 1,
      started_at: "2026-11-20T10:00:00Z", last_activity_at: "2026-11-20T10:05:00Z", current_stage: "assemble", code_attempts: 1, llm_calls: 2,
      tokens_in: 200, tokens_out: 40, cost_usd: "0.0300",
      failure: { event: "generation_failed", state: "NEEDS_FDE_REVIEW", at: "2026-11-20T10:05:00Z", failing_gates: ["AST_IMPORTS"], findings: [{ gate: "AST_IMPORTS", code: "IMPORT_NOT_ALLOWED", file: "b.py", line: 1 }] },
      steps: [
        { seq: 1, kind: "TRANSITION", name: "start_build", status: "OK", started_at: "2026-11-20T10:00:00Z", duration_ms: 120000, from_state: "VALID", to_state: "BUILDING", calls: 0, tokens_in: 0, tokens_out: 0, cost_usd: "0.0000", findings: [] },
        { seq: 2, kind: "MODEL", name: "generate_bodies", status: "FAILED", started_at: "2026-11-20T10:02:00Z", duration_ms: 1500, alias: "sbam-codegen", calls: 2, tokens_in: 200, tokens_out: 40, cost_usd: "0.0300", findings: [] },
      ],
    },
  ],
  ...over,
});

const install = (get: BamApi["getBuildTimeline"]) => setBamApi({ getBuildTimeline: get } as unknown as BamApi);
const renderPanel = (id: string | null = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <TimelinePanel workflowId={id ?? undefined} />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the build timeline in Translate (#926)", () => {
  test("test_wg39_t10_a_parked_build_shows_why_it_stopped_with_gate_and_rule_code_and_the_steps", async () => {
    install(async () => TIMELINE());
    renderPanel();
    const note = await screen.findByRole("note", { name: /Why Verify payment stopped/ });
    expect(within(note).getByText(/Stopped: generation failed/)).toBeTruthy();
    expect(within(note).getByText("IMPORT_NOT_ALLOWED")).toBeTruthy();
    const steps = screen.getByRole("list", { name: "Steps of Verify payment" });
    expect(within(steps).getByText(/generate bodies \(sbam-codegen, 2 calls\)/)).toBeTruthy();
    expect(screen.getByLabelText("Model usage").textContent).toContain("$0.0300");
    expect(screen.getByLabelText("Build totals").textContent).toContain("240 tokens");
  });

  test("test_wg39_t11_loading_shows_a_skeleton_before_the_data", async () => {
    install(() => new Promise(() => undefined));
    renderPanel();
    expect(await screen.findByLabelText("Loading the timeline")).toBeTruthy();
  });

  test("test_wg39_t12_no_sub_skills_is_an_empty_state", async () => {
    install(async () => TIMELINE({ sub_skills: [] }));
    renderPanel();
    expect(await screen.findByText("Nothing has been built")).toBeTruthy();
  });

  test("test_wg39_t13_an_error_shows_the_server_detail_and_retries", async () => {
    let calls = 0;
    install(async () => {
      calls += 1;
      if (calls === 1) throw Object.assign(new Error("x"), { statusCode: 500, problem: { detail: "The timeline is unavailable right now." } });
      return TIMELINE();
    });
    renderPanel();
    expect(await screen.findByText("The timeline is unavailable right now.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByTestId("timeline")).toBeTruthy());
  });

  test("test_wg39_t14_a_forbidden_or_unknown_workflow_is_the_forbidden_state", async () => {
    install(async () => {
      throw Object.assign(new Error("nope"), { statusCode: 404 });
    });
    renderPanel();
    expect(await screen.findByText("You can't see this workflow's timeline")).toBeTruthy();
  });

  test("test_wg39_t15_without_a_workflow_it_says_the_timeline_comes_with_the_build", async () => {
    install(async () => TIMELINE());
    renderPanel(null);
    expect(await screen.findByText("No timeline yet")).toBeTruthy();
  });

  test("test_wg39_t16_durations_read_in_plain_units_and_an_idle_active_build_is_measured", () => {
    expect(formatDuration(250)).toBe("250 ms");
    expect(formatDuration(65_000)).toBe("1 min 5 s");
    expect(formatDuration(null)).toBe("—");
    const sub = { ...TIMELINE().sub_skills[0]!, state: "BUILDING" as const };
    expect(idleMs(sub, Date.parse("2026-11-20T10:15:00Z"))).toBe(10 * 60_000);
    expect(idleMs({ ...sub, state: "CERTIFIED" }, Date.now())).toBeNull();
  });
});
