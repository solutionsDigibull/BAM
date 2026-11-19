import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { StagingDeployment, StagingStatus } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { setUi } from "../lib/store";
import { StagingCenter } from "./Staging";

const P = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const W = "0190f3a2-7b1c-7d4e-8f00-0000000000c1";
const DIGEST = "sha256:" + "cd".repeat(32);
const dep = (over: Partial<StagingDeployment> = {}): StagingDeployment => ({
  version: "1.0.0", registry_name: "wf_onboarding_v1_0_stg", state: "RUNNING" as const, image_digest: DIGEST, ui_bundle_digest: "sha256:" + "ef".repeat(32),
  egress_allowed_hosts: ["api.ledger.example.com"], egress_enforced: true, secret_names: ["LEDGER_API_KEY"], started_at: "2026-11-19T10:00:00Z", ...over,
});
const status = (over: Partial<StagingStatus> = {}): StagingStatus =>
  ({ workflow_id: W, state: "RUNNING", health: "HEALTHY", current: dep(), ui_url: `/api/staging-ui/${P}/${W}/`, history: [dep()], ...over }) as StagingStatus;
const fail = (statusCode: number, detail = "nope") => Promise.reject(Object.assign(new Error(detail), { statusCode, problem: { detail } }));

const install = (api: Partial<BamApi>) =>
  setBamApi({ getChangeNotes: async () => ({ notes: [], used: 0, limit: 3 }), getWorkflowBuilds: async () => ({ sub_skills: [{ sub_skill_id: "s1", skill_id: "MS01.1", skill_name: "Ledger sync" }] }), ...api } as unknown as BamApi);
const renderStaging = (id: string | null = W) =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <StagingCenter workflowId={id ?? undefined} />
      </Refine>
    </div>,
  );

beforeEach(() => {
  window.history.replaceState(null, "", `/projects/${P}/workbench`);
  setUi({ env: "Staging" });
});
afterEach(() => {
  cleanup();
  setBamApi(null);
  setUi({ env: "FDE" });
});

describe("Staging mode shows the generated UI (#861)", () => {
  test("test_861_t01_running_shows_the_status_strip_the_workflow_ui_frame_and_the_history", async () => {
    install({ getStagingStatus: async () => status() });
    renderStaging();
    const frame = await screen.findByTitle("Workflow UI (Staging)");
    expect(frame.getAttribute("src")).toBe(`/api/staging-ui/${P}/${W}/`);
    expect(frame.getAttribute("sandbox")).not.toContain("allow-top-navigation");
    const strip = screen.getByLabelText("Staging status");
    expect(strip.textContent).toContain("Running");
    expect(strip.textContent).toContain("Healthy");
    expect(strip.textContent).toContain("v1.0.0");
    expect(strip.textContent).toContain("sha256:cdcdcdcdcdcd");
    expect(strip.textContent).toContain("api.ledger.example.com");
    expect(strip.textContent).toContain("LEDGER_API_KEY"); // a name, never a value
    expect(screen.getByLabelText("Version history").textContent).toContain("v1.0.0");
  });

  test("test_861_t02_fde_mode_renders_no_workflow_ui_and_makes_no_staging_call", async () => {
    setUi({ env: "FDE" });
    const read = vi.fn(async () => status());
    install({ getStagingStatus: read });
    const { container } = renderStaging();
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.queryByTitle("Workflow UI (Staging)")).toBeNull();
    expect(read).not.toHaveBeenCalled();
  });

  test("test_861_t03_pending_runtime_shows_the_detail_and_how_to_try_again", async () => {
    install({ getStagingStatus: async () => status({ state: "PENDING_RUNTIME", health: "UNKNOWN", ui_url: null, current: dep({ state: "PENDING_RUNTIME", detail: "the Staging runtime is not configured" }) }) });
    const { container } = renderStaging();
    await screen.findByText(/Waiting for the Staging runtime\./);
    expect(screen.getByText(/the Staging runtime is not configured/)).toBeTruthy();
    expect(screen.getByText(/Commit again/)).toBeTruthy();
    expect(container.querySelector("iframe")).toBeNull();
  });

  test("test_861_t04_failed_shows_the_detail_and_a_way_back_to_commit", async () => {
    install({ getStagingStatus: async () => status({ state: "FAILED", ui_url: null, current: dep({ state: "FAILED", detail: "IMAGE_MISSING: the host does not hold the image" }) }) });
    renderStaging();
    await screen.findByText("Staging could not start the workflow");
    expect(screen.getByText(/IMAGE_MISSING/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Commit again/ })).toBeTruthy();
  });

  test("test_861_t05_superseded_and_stopped_show_a_status_instead_of_the_ui", async () => {
    install({ getStagingStatus: async () => status({ state: "SUPERSEDED", ui_url: null, current: dep({ state: "SUPERSEDED", superseded_by: "1.0.1" }) }) });
    const { container } = renderStaging();
    await screen.findByText("This version was superseded");
    expect(screen.getByText(/Version 1.0.1 replaced it/)).toBeTruthy();
    expect(container.querySelector("iframe")).toBeNull();
  });

  test("test_861_t06_unreachable_health_warns_above_the_ui", async () => {
    install({ getStagingStatus: async () => status({ health: "UNREACHABLE" }) });
    renderStaging();
    await screen.findByText(/The workflow server does not answer/);
    expect(screen.getByTitle("Workflow UI (Staging)")).toBeTruthy();
  });

  test("test_861_t07_a_404_is_the_not_committed_or_not_yours_state", async () => {
    install({ getStagingStatus: () => fail(404, "Not found") });
    renderStaging();
    await screen.findByText("Nothing in Staging yet");
    expect(screen.queryByTitle("Workflow UI (Staging)")).toBeNull();
  });

  test("test_861_t08_a_403_is_the_forbidden_state_and_a_server_error_has_a_retry", async () => {
    install({ getStagingStatus: () => fail(403) });
    renderStaging();
    await screen.findByText("You can't see Staging for this project");
    cleanup();
    install({ getStagingStatus: () => fail(503, "The runtime is down") });
    renderStaging();
    await screen.findByText("Could not load Staging");
    expect(screen.getByText("The runtime is down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  test("test_861_t09_loading_and_no_workflow_have_their_own_states", async () => {
    install({ getStagingStatus: () => new Promise(() => {}) });
    renderStaging();
    expect(await screen.findByRole("status", { name: "Loading Staging" })).toBeTruthy();
    cleanup();
    renderStaging(null);
    expect(screen.getByText("Not committed yet")).toBeTruthy();
  });
});

describe("Request change from Staging (#767, ADR 0032 decision 5)", () => {
  test("test_861_t10_a_note_is_sent_with_the_sub_skill_and_an_idempotency_key", async () => {
    const send = vi.fn(async () => ({ note: { id: "n1" } }));
    install({ getStagingStatus: async () => status(), requestChange: send as never });
    renderStaging();
    await screen.findByTitle("Workflow UI (Staging)");
    const submit = await screen.findByRole("button", { name: "Send note" });
    await waitFor(() => expect((screen.getByLabelText("Sub-Skill") as HTMLSelectElement).value).toBe("s1"));
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("What is wrong"), { target: { value: "The total ignores tax." } });
    fireEvent.click(submit);
    await screen.findByText(/The note was sent/);
    expect(send).toHaveBeenCalledWith(W, { sub_skill_id: "s1", note: "The total ignores tax." }, expect.stringMatching(/^[0-9a-f-]{36}$/));
  });

  test("test_861_t11_the_fourth_note_is_refused_with_guidance_to_the_isatvon_grid", async () => {
    const send = vi.fn(() => fail(409, "This workflow version already has 3 change notes."));
    install({ getStagingStatus: async () => status(), requestChange: send as never });
    renderStaging();
    await screen.findByTitle("Workflow UI (Staging)");
    await waitFor(() => expect((screen.getByLabelText("Sub-Skill") as HTMLSelectElement).value).toBe("s1"));
    fireEvent.change(screen.getByLabelText("What is wrong"), { target: { value: "Another problem." } });
    fireEvent.click(screen.getByRole("button", { name: "Send note" }));
    expect((await screen.findAllByText(/already has 3 change notes/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Open the ISATVON Grid" })).toBeTruthy();
    expect((screen.getByLabelText("What is wrong") as HTMLTextAreaElement).disabled).toBe(true);
  });

  test("test_861_t12_a_version_with_all_three_notes_used_starts_closed", async () => {
    install({ getStagingStatus: async () => status(), getChangeNotes: async () => ({ notes: [], used: 3, limit: 3 }) });
    renderStaging();
    await screen.findByTitle("Workflow UI (Staging)");
    await screen.findByText("3 of 3 used");
    expect((screen.getByRole("button", { name: "Send note" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
