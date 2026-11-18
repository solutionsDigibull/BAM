import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { RepoScanReport, WorkflowRepositories, WorkflowRepository } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { RepositoryPanel } from "./RepositoryPanel";

const COMMIT = "a".repeat(40);
const REPORT = {
  url: "https://github.com/acme/lib", host: "github.com", commit: COMMIT, decision: "VENDORED",
  licence: { spdx: "MIT", source: "LICENSE", verdict: "ALLOWED" },
  dependency_licences: [{ ecosystem: "PyPI", name: "gpl-thing", version: "1.0", spdx: "GPL-3.0", verdict: "REFUSED" }],
  vulnerabilities: [
    { ecosystem: "PyPI", package: "old", version: "0.1", id: "CVE-2026-0002", summary: "minor", severity: "LOW" },
    { ecosystem: "PyPI", package: "bad", version: "1.2", id: "CVE-2026-0001", summary: "remote code", severity: "CRITICAL" },
  ],
  static_findings: [{ tool: "bandit", rule: "B602", path: "lib/run.py", line: 12, severity: "HIGH", message: "shell=True" }],
  clam_findings: [{ path: "lib/x.bin", signature: "Test.Signature" }],
  files_scanned: 41, unsafe_paths: [], health: { last_commit_at: "2026-05-01T10:00:00Z", archived: false, flags: ["STALE"] }, notes: [],
} as unknown as RepoScanReport;
const REPO: WorkflowRepository = {
  url: "https://github.com/acme/lib", commit: COMMIT, licence: "MIT", repo_dir: `vendor/lib@${COMMIT}`, sub_skills: ["MS01.1"],
  patches: [{ path: `patches/lib@${COMMIT}/0001-fix.patch`, sha256: "1".repeat(64), reason: "Use the project logger", files: ["lib/run.py"] }],
  report: REPORT,
  sbom: [{ kind: "repository", name: "acme/lib", version: COMMIT, licence: "MIT" }, { kind: "dependency", name: "gpl-thing", version: "1.0", ecosystem: "PyPI", licence: "GPL-3.0" }],
};
const DATA = (repositories: WorkflowRepository[]): WorkflowRepositories => ({ workflow_id: "w1", repositories });

const install = (over: Partial<BamApi>) => setBamApi({ getWorkflowRepositories: async () => DATA([REPO]), ...over } as unknown as BamApi);
const renderPanel = (id: string | undefined = "w1") =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <RepositoryPanel workflowId={id} />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("Translate shows the repositories the workflow starts from, read only (#843)", () => {
  test("test_843_the_report_shows_commit_licence_findings_malware_and_health", async () => {
    install({});
    renderPanel();
    const card = await screen.findByRole("region", { name: "acme/lib" });
    expect(card.textContent).toContain("aaaaaaaaaaaa"); // the short commit
    expect(card.textContent).toContain("MIT · Allowed");
    expect(card.textContent).toContain("Needs attention"); // a refused dependency licence, a CRITICAL finding and malware
    expect(within(card).getByRole("list", { name: "Dependencies with a refused licence" }).textContent).toContain("gpl-thing@1.0");
    const vulns = within(card).getByRole("list", { name: "Vulnerabilities" });
    const rows = within(vulns).getAllByRole("listitem");
    expect(rows[0]!.textContent).toContain("CVE-2026-0001"); // CRITICAL before LOW
    expect(rows[1]!.textContent).toContain("CVE-2026-0002");
    expect(within(card).getByRole("list", { name: "Bandit and pattern findings" }).textContent).toContain("lib/run.py:12");
    expect(within(card).getByRole("list", { name: "Quarantined files" }).textContent).toContain("Test.Signature");
    expect(card.textContent).toContain("Last commit 2026-05-01");
    expect(card.textContent).toContain("STALE");
  });

  test("test_843_the_patches_are_listed_with_their_reasons_and_the_components_with_their_versions", async () => {
    install({});
    renderPanel();
    const patches = await screen.findByRole("list", { name: "Patches" });
    expect(patches.textContent).toContain("0001-fix.patch");
    expect(patches.textContent).toContain("Use the project logger");
    expect(patches.textContent).toContain("lib/run.py");
    const sbom = screen.getByRole("list", { name: "Components" });
    expect(sbom.textContent).toContain("acme/lib");
    expect(sbom.textContent).toContain("aaaaaaaaaaaa"); // the repository's version is its commit
    expect(sbom.textContent).toContain("gpl-thing");
    expect(sbom.textContent).toContain("GPL-3.0");
    expect(screen.getByRole("status").textContent).toMatch(/1 repository · 1 with a scan report · 1 need attention · 1 patch · 2 components/);
  });

  test("test_843_a_repository_without_a_stored_report_is_listed_with_a_notice_not_an_error", async () => {
    install({ getWorkflowRepositories: async () => DATA([{ ...REPO, report: null, patches: [] }]) });
    renderPanel();
    const card = await screen.findByRole("region", { name: "acme/lib" });
    expect(card.textContent).toContain("No scan report");
    expect(card.textContent).toMatch(/Certification needs one/);
    expect(card.textContent).toContain("Used as is: no patch was applied.");
    expect(screen.queryByText("Could not load the repositories")).toBeNull();
  });

  test("test_843_a_workflow_without_a_repository_shows_the_empty_state", async () => {
    install({ getWorkflowRepositories: async () => DATA([]) });
    renderPanel();
    expect(await screen.findByText("No repository")).toBeTruthy();
    expect(screen.getByText(/No repository was used, or no scan report is attached/)).toBeTruthy();
  });

  test("test_843_loading_then_an_error_with_the_servers_detail_and_a_retry_that_reloads", async () => {
    const get = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("Boom"), { statusCode: 500, problem: { detail: "Storage is unavailable" } }))
      .mockResolvedValue(DATA([REPO]));
    install({ getWorkflowRepositories: get });
    renderPanel();
    expect(screen.getByLabelText("Loading the repositories")).toBeTruthy();
    expect(await screen.findByText("Could not load the repositories")).toBeTruthy();
    expect(screen.getByText("Storage is unavailable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "acme/lib" })).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(2);
  });

  test("test_843_another_tenants_workflow_or_an_unassigned_fde_sees_the_forbidden_state", async () => {
    install({ getWorkflowRepositories: () => Promise.reject(Object.assign(new Error("Not found"), { statusCode: 404 })) });
    renderPanel();
    expect(await screen.findByText("You can't see this workflow's repositories")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  test("test_843_before_the_build_has_started_there_is_no_workflow_to_ask_about", () => {
    const get = vi.fn();
    install({ getWorkflowRepositories: get });
    renderPanel(""); // no workflow id yet (the default parameter would replace undefined)
    expect(screen.getByText("No repository yet")).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
  });

  test("test_843_the_panel_is_read_only_and_offers_no_form_control_to_change_anything", async () => {
    install({});
    renderPanel();
    await screen.findByRole("region", { name: "acme/lib" });
    expect(document.querySelectorAll("input, textarea, select, form")).toHaveLength(0);
  });
});
