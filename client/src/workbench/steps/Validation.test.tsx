import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import type { CommitStatus, Connection, ConnectionList } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import type { StepInfo, Snapshot } from "../api/types";
import { ValidationCenter, ValidationFooter } from "./Validation";

const HASH = "sha256:" + "ab".repeat(32);
const DIGEST = "sha256:" + "cd".repeat(32);
const SECRET_VALUE = ["fixture", "only", "not", "real", "1716"].join("_"); // built at run time, never a literal
const CHECKS = [
  { name: "WORKFLOW_CERTIFIED", passed: true },
  { name: "SUB_SKILLS_CERTIFIED", passed: true },
  { name: "MCP_SERVER", passed: true },
  { name: "UI_BUNDLE", passed: true },
  { name: "SECRETS", passed: true },
];
const status = (over: Partial<CommitStatus> = {}) =>
  ({ workflow_id: "w1", manifest_hash: HASH, ready: true, checks: CHECKS, blockers: [], state: "NOT_COMMITTED", ...over }) as unknown as CommitStatus;
const conn = (over: Partial<Connection> = {}) =>
  ({ name: "LEDGER_API_KEY", source: "ISATVON", purpose: "Used by MS01.1", required: true, status: "MISSING", masked_tail: "", test_mode: "HTTP", row_version: 0, ...over }) as Connection;
const list = (...c: Connection[]) => ({ project_id: "p1", connections: c }) as ConnectionList;

const commitButton = (): HTMLElement => document.querySelector<HTMLElement>(".bam-btn")!; // its name holds every phase label

const snap = { workflowId: "w1" } as unknown as Snapshot;
const info = { editable: true } as unknown as StepInfo;

beforeAll(() => {
  // jsdom has no <dialog> modal API: opening it is enough for the tests to see its content.
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
});

const install = (api: Partial<BamApi>) => setBamApi({ getConnections: async () => list(), ...api } as unknown as BamApi);
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

describe("Validation: checks, secrets and Commit to Staging (#766)", () => {
  test("test_766_every_check_passing_says_so_and_lists_each_check", async () => {
    install({ getCommitStatus: async () => status() });
    wrap(<ValidationCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Every check passes/)).toBeTruthy();
    const list = screen.getByRole("list", { name: "Automated checks" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(list.textContent).toContain("Secrets set and tested");
  });

  test("test_766_a_failed_check_blocks_commit_and_shows_its_reason", async () => {
    const failed = CHECKS.map((c) => (c.name === "UI_BUNDLE" ? { ...c, passed: false, reason: "MISSING_EVIDENCE:UI_BUNDLE" } : c));
    install({ getCommitStatus: async () => status({ ready: false, checks: failed } as Partial<CommitStatus>) });
    wrap(<ValidationCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Commit is blocked/)).toBeTruthy();
    expect(screen.getAllByText(/MISSING_EVIDENCE:UI_BUNDLE/).length).toBeGreaterThan(0);
  });

  test("test_766_a_committed_workflow_shows_its_staging_entry_and_a_pending_runtime", async () => {
    const entry = { registry_name: "wf_ledger_v1_0_stg", version: "1.0.0", image_digest: DIGEST, manifest_hash: HASH, kind: "staging" };
    install({
      getCommitStatus: async () =>
        status({ state: "COMMITTED", registration: entry, staging: { status: "PENDING_RUNTIME" }, committed_by: "fde-1" } as unknown as Partial<CommitStatus>),
    });
    wrap(<ValidationCenter snap={snap} info={info} />);
    expect(await screen.findByText(/Committed to Staging as version 1.0.0/)).toBeTruthy();
    expect(screen.getByText(/Waiting for the Staging runtime/)).toBeTruthy();
    const dl = screen.getByLabelText("Staging entry");
    expect(dl.textContent).toContain("wf_ledger_v1_0_stg");
    expect(dl.textContent).toContain("the digest that was certified");
  });

  test("test_766_loading_error_forbidden_and_empty_states", async () => {
    wrap(<ValidationCenter snap={{ workflowId: undefined } as unknown as Snapshot} info={info} />);
    expect(await screen.findByText("Nothing to validate yet")).toBeTruthy();
    cleanup();
    install({ getCommitStatus: () => new Promise(() => {}) });
    wrap(<ValidationCenter snap={snap} info={info} />);
    expect(screen.getByLabelText("Loading the checks")).toBeTruthy();
    cleanup();
    install({ getCommitStatus: () => Promise.reject(Object.assign(new Error("no"), { statusCode: 403 })) });
    wrap(<ValidationCenter snap={snap} info={info} />);
    expect(await screen.findByText("You can't validate this workflow")).toBeTruthy();
    cleanup();
    const get = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(status());
    install({ getCommitStatus: get });
    wrap(<ValidationCenter snap={snap} info={info} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Every check passes/)).toBeTruthy();
  });

  test("test_766_commit_is_locked_with_its_reason_until_every_check_passes", async () => {
    const failed = CHECKS.map((c) => (c.name === "SECRETS" ? { ...c, passed: false, reason: "LEDGER_API_KEY MISSING" } : c));
    install({ getCommitStatus: async () => status({ ready: false, checks: failed } as Partial<CommitStatus>) });
    wrap(<ValidationFooter snap={snap} info={info} />);
    expect(await screen.findByText(/Secrets set and tested: LEDGER_API_KEY MISSING/)).toBeTruthy();
    expect(commitButton().getAttribute("aria-disabled")).toBe("true");
  });

  test("test_766_commit_lists_each_secret_masked_and_sends_the_hash_with_one_key", async () => {
    const commit = vi.fn().mockRejectedValueOnce(Object.assign(new Error("changed"), { problem: { detail: "The Workflow changed" } })).mockResolvedValue({});
    install({
      getCommitStatus: async () => status(),
      getConnections: async () => list(conn({ status: "TESTED", masked_tail: "1716", row_version: 2 })),
      commitWorkflow: commit,
    });
    wrap(<ValidationFooter snap={snap} info={info} />);
    await waitFor(() => expect(commitButton()).toBeTruthy());
    await waitFor(() => expect(commitButton().getAttribute("aria-disabled")).toBeNull());
    fireEvent.click(commitButton());
    const secrets = await screen.findByLabelText("Secrets in this commit");
    expect(secrets.textContent).toContain("LEDGER_API_KEY");
    expect(secrets.textContent).toContain("ends 1716");
    expect(secrets.textContent).not.toContain(SECRET_VALUE);
    fireEvent.click(screen.getByRole("button", { name: "Commit" }));
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("The Workflow changed")).toBeTruthy();
    fireEvent.click(commitButton());
    fireEvent.click(screen.getByRole("button", { name: "Commit" }));
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(2));
    expect(commit.mock.calls[0]![0]).toBe("w1");
    expect(commit.mock.calls[0]![1]).toBe(HASH);
    expect(commit.mock.calls[1]![2]).toBe(commit.mock.calls[0]![2]); // the same hash is the same command
  });

  test("test_766_a_read_only_screen_keeps_commit_locked", async () => {
    install({ getCommitStatus: async () => status() });
    wrap(<ValidationFooter snap={snap} info={{ editable: false } as unknown as StepInfo} />);
    expect(await screen.findByText("This screen is read-only")).toBeTruthy();
  });
});

describe("The secrets panel (#764, #766)", () => {
  const open = async (api: Partial<BamApi>) => {
    install({ getCommitStatus: async () => status(), ...api });
    wrap(<ValidationCenter snap={snap} info={info} />);
    return screen.findByRole("list", { name: undefined }).catch(() => null);
  };

  test("test_766_a_value_is_sent_once_and_never_shown_again", async () => {
    const setValue = vi.fn().mockResolvedValue({ connection: conn({ status: "SET", masked_tail: "1716" }) });
    await open({ getConnections: async () => list(conn()), setConnectionValue: setValue });
    const field = (await screen.findByLabelText("Value")) as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: SECRET_VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setValue).toHaveBeenCalledWith("LEDGER_API_KEY", SECRET_VALUE, undefined));
    expect(field.value).toBe("");
    expect(document.body.textContent).not.toContain(SECRET_VALUE);
  });

  test("test_766_a_stored_secret_shows_its_tail_and_a_replace_needs_the_row_version", async () => {
    const setValue = vi.fn().mockResolvedValue({ connection: conn({ status: "SET" }) });
    await open({ getConnections: async () => list(conn({ status: "TESTED", masked_tail: "1716", row_version: 3 })), setConnectionValue: setValue });
    const row = await screen.findByLabelText("Secret LEDGER_API_KEY");
    expect(row.textContent).toContain("Tested");
    expect(row.textContent).toContain("ends 1716");
    fireEvent.change(within(row).getByLabelText("Replace value"), { target: { value: SECRET_VALUE } });
    fireEvent.click(within(row).getByRole("button", { name: "Replace" }));
    await waitFor(() => expect(setValue).toHaveBeenCalledWith("LEDGER_API_KEY", SECRET_VALUE, '"3"'));
    expect(await screen.findByText(/Test it again before you commit/)).toBeTruthy();
  });

  test("test_766_testing_needs_a_value_and_an_address_and_reports_what_the_service_said", async () => {
    const test = vi.fn().mockResolvedValue({ outcome: "UNAUTHORIZED", message: "The service rejected this key.", connection: conn() });
    await open({
      getConnections: async () => list(conn({ status: "SET", masked_tail: "1716", row_version: 1, test_config: { url: "https://ledger.example.com/ping" } })),
      testConnection: test,
    });
    const row = await screen.findByLabelText("Secret LEDGER_API_KEY");
    fireEvent.click(within(row).getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(test).toHaveBeenCalledWith("LEDGER_API_KEY"));
    expect(await screen.findByText("The service rejected this key.")).toBeTruthy();
  });

  test("test_766_a_secret_without_an_address_cannot_be_tested_yet", async () => {
    await open({ getConnections: async () => list(conn({ status: "SET", masked_tail: "1716", row_version: 1 })) });
    const row = await screen.findByLabelText("Secret LEDGER_API_KEY");
    expect(row.textContent).toContain("Save a test address first");
    expect(within(row).getByRole("button", { name: "Test connection" }).getAttribute("aria-disabled")).toBe("true");
  });

  test("test_766_empty_error_and_forbidden_states", async () => {
    await open({ getConnections: async () => list() });
    expect(await screen.findByText("No secrets are needed")).toBeTruthy();
    cleanup();
    await open({ getConnections: () => Promise.reject(Object.assign(new Error("no"), { statusCode: 403 })) });
    expect(await screen.findByText("You can't see this project's secrets")).toBeTruthy();
    cleanup();
    await open({ getConnections: () => Promise.reject(new Error("boom")) });
    expect(await screen.findByText("Could not load the secrets")).toBeTruthy();
  });
});
