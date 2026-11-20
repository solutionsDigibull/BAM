import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test } from "vitest";

import type { CommitStatus, StagingStatus, WorkflowVersionList } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import type { StepInfo, Snapshot } from "../api/types";
import { getUi } from "../lib/store";
import { CertifyCenter, CertifyFooter, CommitCenter, StagingCompleteCenter, StagingCompleteFooter } from "./Release";

const HASH = "sha256:" + "ab".repeat(32);
const DIGEST = "sha256:" + "cd".repeat(32);
const CHECKS = [
  { name: "WORKFLOW_CERTIFIED", passed: true },
  { name: "SUB_SKILLS_CERTIFIED", passed: true },
  { name: "UI_BUNDLE", passed: true },
  { name: "SECRETS", passed: false, reason: "LEDGER_API_KEY is not set" },
];
const status = (over: Partial<CommitStatus> = {}) =>
  ({ workflow_id: "w1", manifest_hash: HASH, ready: false, checks: CHECKS, blockers: [], state: "NOT_COMMITTED", ...over }) as unknown as CommitStatus;
const problem = (statusCode: number, detail = "x") => Object.assign(new Error(detail), { statusCode, problem: { detail } });
const versions = (...items: Partial<WorkflowVersionList["items"][number]>[]) =>
  ({ items: items.map((v, i) => ({ id: `v${i}`, workflow_id: "w1", version: "1.0.0", sow_version_id: "s", workflow_manifest_id: "m", change_reason: "", frozen_at: "2026-11-20T08:00:00Z", superseded: false, ...v })) }) as WorkflowVersionList;
const staging = (over: Partial<StagingStatus> = {}) =>
  ({ workflow_id: "w1", state: "RUNNING", health: "HEALTHY", current: { version: "1.0.0", registry_name: "wf_ledger_v1_0_stg", state: "RUNNING", image_digest: DIGEST }, history: [], ...over }) as unknown as StagingStatus;

const snap = { workflowId: "w1" } as unknown as Snapshot;
const none = { workflowId: undefined } as unknown as Snapshot;
const info = { editable: true } as unknown as StepInfo;

const install = (api: Partial<BamApi>) => setBamApi({ getConnections: async () => ({ project_id: "p1", connections: [] }), ...api } as unknown as BamApi);
const wrap = (node: React.ReactNode) =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        {node}
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("Certify (4.5): automatic certification, read only (#824)", () => {
  test("test_824_certified_shows_only_the_certification_checks", async () => {
    install({ getCommitStatus: async () => status() });
    wrap(<CertifyCenter snap={snap} info={info} />);
    expect(await screen.findByText(/^Certified\./)).toBeTruthy();
    const list = screen.getByRole("list", { name: "Automated checks" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2); // the secrets check is Commit's, not certification's
  });

  test("test_824_a_failed_certification_check_says_not_certified", async () => {
    const failed = CHECKS.map((c) => (c.name === "SUB_SKILLS_CERTIFIED" ? { ...c, passed: false, reason: "MS01.1 not certified" } : c));
    install({ getCommitStatus: async () => status({ checks: failed } as Partial<CommitStatus>) });
    wrap(<CertifyCenter snap={snap} info={info} />);
    expect(await screen.findByText(/^Not certified\./)).toBeTruthy();
    expect(screen.getByText(/MS01.1 not certified/)).toBeTruthy();
  });

  test("test_824_certification_still_running_says_so", async () => {
    install({ getCommitStatus: async () => status({ checks: [] } as Partial<CommitStatus>) });
    wrap(<CertifyCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Certification has not finished/)).toBeTruthy();
  });

  test("test_824_certify_forbidden_and_not_found_and_empty_states", async () => {
    install({ getCommitStatus: async () => { throw problem(403); } });
    wrap(<CertifyCenter snap={snap} info={info} />);
    expect(await screen.findByText(/You can't certify this workflow/)).toBeTruthy();
    cleanup();
    install({ getCommitStatus: async () => { throw problem(404); } });
    wrap(<CertifyCenter snap={snap} info={info} />);
    expect(await screen.findByText(/This workflow was not found/)).toBeTruthy();
    cleanup();
    wrap(<CertifyCenter snap={none} info={info} />);
    expect(screen.getByText(/Nothing to certify yet/)).toBeTruthy();
  });

  test("test_824_certify_has_no_approve_action_only_open_commit", async () => {
    install({ getCommitStatus: async () => status() });
    wrap(<CertifyFooter snap={snap} info={info} />);
    expect(await screen.findByText("Certified")).toBeTruthy();
    expect(screen.queryByText("Approve")).toBeNull();
    expect(screen.getByText("Open Commit")).toBeTruthy();
  });
});

describe("Commit (4.6): the frozen versions and the Staging entry (#824)", () => {
  test("test_824_commit_lists_the_frozen_versions", async () => {
    install({ getCommitStatus: async () => status(), getWorkflowVersions: async () => versions({ version: "1.0.1", change_reason: "Fix MS01.1" }, { version: "1.0.0", superseded: true }) });
    wrap(<CommitCenter snap={snap} info={info} />);
    const table = await screen.findByRole("table", { name: "Frozen versions" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]!.textContent).toContain("1.0.1");
    expect(rows[1]!.textContent).toContain("Current");
    expect(rows[2]!.textContent).toContain("Superseded");
  });

  test("test_824_commit_with_nothing_frozen_yet_and_a_blocked_check", async () => {
    install({ getCommitStatus: async () => status(), getWorkflowVersions: async () => versions() });
    wrap(<CommitCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Nothing frozen yet/)).toBeTruthy();
    expect(screen.getByText(/Commit is blocked/)).toBeTruthy();
  });

  test("test_824_a_committed_workflow_leads_to_staging_complete", async () => {
    const entry = { registry_name: "wf_ledger_v1_0_stg", version: "1.0.0", image_digest: DIGEST, manifest_hash: HASH, kind: "staging" };
    install({
      getCommitStatus: async () => status({ state: "COMMITTED", registration: entry, staging: { status: "STARTED" } } as unknown as Partial<CommitStatus>),
      getWorkflowVersions: async () => versions({ version: "1.0.0" }),
    });
    wrap(<CommitCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Committed to Staging as version 1.0.0/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Staging status" })).toBeTruthy();
  });

  test("test_824_the_frozen_versions_error_offers_retry", async () => {
    install({ getCommitStatus: async () => status(), getWorkflowVersions: async () => { throw problem(500, "storage down"); } });
    wrap(<CommitCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Could not load the frozen versions/)).toBeTruthy();
    expect(screen.getByText(/storage down/)).toBeTruthy();
  });
});

describe("Staging complete (4.7): what runs in Staging (#824)", () => {
  test("test_824_running_in_staging_and_open_staging_switches_the_toggle", async () => {
    install({ getStagingStatus: async () => staging() });
    wrap(<><StagingCompleteCenter snap={snap} info={info} /><StagingCompleteFooter snap={snap} info={info} /></>);
    expect(await screen.findByText(/^Staging complete\./)).toBeTruthy();
    expect(screen.getByLabelText("Staging deployment").textContent).toContain("wf_ledger_v1_0_stg");
    fireEvent.click(await screen.findByText("Open Staging"));
    expect(getUi().env).toBe("Staging");
  });

  test("test_824_never_committed_is_an_empty_state_and_pending_runtime_waits", async () => {
    install({ getStagingStatus: async () => { throw problem(404); } });
    wrap(<StagingCompleteCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Nothing on Staging yet/)).toBeTruthy();
    cleanup();
    install({ getStagingStatus: async () => staging({ state: "PENDING_RUNTIME", health: "UNKNOWN" }) });
    wrap(<StagingCompleteCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Waiting for the Staging runtime\./)).toBeTruthy();
  });

  test("test_824_staging_forbidden_and_error_states", async () => {
    install({ getStagingStatus: async () => { throw problem(403); } });
    wrap(<StagingCompleteCenter snap={snap} info={info} />);
    expect(await screen.findByText(/You can't see this Staging deployment/)).toBeTruthy();
    cleanup();
    install({ getStagingStatus: async () => { throw problem(500, "runtime unreachable"); } });
    wrap(<StagingCompleteCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Could not load the Staging status/)).toBeTruthy();
  });
});
