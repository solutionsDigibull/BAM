import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BuildsResponse } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { reminderText, rowStatusLabel, waitedFor } from "../lib/workflowBuilds";
import { BuildsPanel, WaitingNotice } from "./BuildsPanel";

// WG-43 (#931): a build that waits for the FDE's repository answer must not look like BUILDING.
const SUB = "0190f3a2-7b1c-7d4e-8f00-0000000000d1";
const SINCE = "2026-11-17T09:00:00Z";
const waiting = (reminders = 0) => ({
  kind: "REPO_QUESTION" as const,
  since: SINCE,
  question_kind: "CLOSE_CANDIDATES" as const,
  reminders_sent: reminders,
  next_reminder_at: "2026-11-20T09:00:00Z",
  link: "/api/v1/projects/p/workflows/w1/repo-questions",
});
const BUILDS = (reminders = 0, withWaiting = true): BuildsResponse => ({
  workflow_id: "w1",
  workflow_state: "BUILDING",
  active: true,
  sub_skills: [
    { sub_skill_id: SUB, skill_id: "MS01.1", skill_name: "Verify payment", state: "BUILDING", stage: withWaiting ? "wait_repo_choice" : "generate_bodies", attempt: 0, gates: {}, ...(withWaiting ? { waiting_for: waiting(reminders) } : {}) },
  ],
  workflow_gates: {},
});
const install = (over: Partial<BamApi> = {}) =>
  setBamApi({ getWorkflowBuilds: async () => BUILDS(), getSubSkillGates: async () => ({ sub_skill_id: SUB, attempt: 0, gates: [] }), getHeldPatch: async () => ({ held: null }), ...over } as unknown as BamApi);
const wrap = (ui: React.ReactNode) =>
  render(<div className="bam-wb"><Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>{ui}</Refine></div>);

afterEach(() => { cleanup(); setBamApi(null); });

describe("a build waiting for the repository answer (#931)", () => {
  test("test_wg43_t08_a_waiting_row_says_waiting_for_your_answer_not_building", async () => {
    install();
    wrap(<BuildsPanel workflowId="w1" />);
    const note = await screen.findByTestId(`waiting-${SUB}`);
    const row = note.closest("li.bld-row") as HTMLElement;
    expect(row.textContent).toContain("Waiting for your answer");
    expect(row.textContent).not.toContain("Building");
    expect(note.textContent).toContain("which repository should it start from");
    expect(note.textContent).toContain("2026-11-17 09:00 UTC");
    expect(screen.getByRole("group", { name: "Build summary" }).textContent).toContain("1 waiting for your answer");
  });

  test("test_wg43_t09_the_reminder_line_says_how_many_were_sent_and_when_the_next_is_due", () => {
    expect(reminderText({ reminders_sent: 0, next_reminder_at: "2026-11-20T09:00:00Z" })).toContain("No reminder sent yet");
    expect(reminderText({ reminders_sent: 1, next_reminder_at: "2026-11-23T09:00:00Z" })).toContain("1 reminder sent");
    expect(reminderText({ reminders_sent: 2, next_reminder_at: "2026-11-23T09:00:00Z" })).toContain("2 reminders sent");
    expect(waitedFor(SINCE, Date.parse("2026-11-20T09:00:00Z"))).toBe("3 days ago");
    expect(waitedFor(SINCE, Date.parse("2026-11-17T14:00:00Z"))).toBe("5 hours ago");
    expect(waitedFor(SINCE, Date.parse("2026-11-17T09:00:10Z"))).toBe("just now");
  });

  test("test_wg43_t10_the_button_goes_to_the_question_card", async () => {
    install();
    const card = document.createElement("section");
    card.id = `rq-${SUB}`;
    card.tabIndex = -1;
    card.scrollIntoView = vi.fn();
    document.body.appendChild(card);
    wrap(<BuildsPanel workflowId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Answer the question for Verify payment" }));
    expect(card.scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(card);
    card.remove();
  });

  test("test_wg43_t11_when_the_card_is_not_on_the_page_the_button_says_where_to_go", async () => {
    install();
    wrap(<BuildsPanel workflowId="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Answer the question for Verify payment" }));
    expect((await screen.findByTestId(`waiting-${SUB}`)).textContent).toContain("Open the Repositories view");
  });

  test("test_wg43_t12_a_build_that_is_not_waiting_keeps_its_own_status", async () => {
    install({ getWorkflowBuilds: async () => BUILDS(0, false) });
    wrap(<BuildsPanel workflowId="w1" />);
    const list = await screen.findByRole("list", { name: "Sub-Skill builds" });
    expect(list.textContent).toContain("Building");
    expect(screen.queryByTestId(`waiting-${SUB}`)).toBeNull();
    expect(rowStatusLabel({ state: "BUILDING" })).toBe("Building");
  });

  test("test_wg43_t13_the_panel_keeps_its_error_and_forbidden_states", async () => {
    install({ getWorkflowBuilds: async () => { throw Object.assign(new Error("boom"), { problem: { status: 404, detail: "Workflow not found" } }); } });
    wrap(<BuildsPanel workflowId="w1" />);
    expect(await screen.findByText(/can't see this workflow's builds|Could not load the builds/)).toBeTruthy();
  });

  test("test_wg43_t14_the_notice_shows_only_while_a_build_waits", async () => {
    install();
    wrap(<WaitingNotice workflowId="w1" />);
    expect((await screen.findByTestId("waiting-notice")).textContent).toContain("Verify payment is waiting for your answer");
    cleanup();
    install({ getWorkflowBuilds: async () => BUILDS(0, false) });
    wrap(<WaitingNotice workflowId="w1" />);
    await new Promise(r => setTimeout(r, 30));
    expect(screen.queryByTestId("waiting-notice")).toBeNull();
  });
});
