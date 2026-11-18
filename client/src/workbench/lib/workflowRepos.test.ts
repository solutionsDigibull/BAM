import { describe, expect, test } from "vitest";

import type { RepoScanReport, WorkflowRepository } from "@/contracts";

import {
  healthLine, refusalText, repoName, reportBlocks, severityCounts, severityTone, shortCommit, sortBySeverity, totals, verdictTone, where,
} from "./workflowRepos";

const COMMIT = "a".repeat(40);
const report = (over: Partial<RepoScanReport> = {}): RepoScanReport => ({
  url: "https://github.com/acme/lib", host: "github.com", commit: COMMIT, decision: "VENDORED",
  licence: { spdx: "MIT", source: "LICENSE", verdict: "ALLOWED" }, health: {}, ...over,
}) as RepoScanReport;
const repo = (over: Partial<WorkflowRepository> = {}): WorkflowRepository => ({
  url: "https://github.com/acme/lib", commit: COMMIT, repo_dir: `vendor/lib@${COMMIT}`, sub_skills: ["MS01.1"], patches: [], sbom: [], ...over,
});

describe("repository panel helpers (#843)", () => {
  test("test_843_names_and_commits_are_shortened_for_display", () => {
    expect(repoName("https://github.com/acme/lib")).toBe("acme/lib");
    expect(repoName("https://gitlab.com/group/sub/lib/")).toBe("sub/lib");
    expect(shortCommit(COMMIT)).toBe("a".repeat(12));
    expect(where({ path: "a.py", line: 3 })).toBe("a.py:3");
    expect(where({ path: "a.py", line: 0 })).toBe("a.py");
  });

  test("test_843_high_and_critical_are_the_findings_that_block_and_come_first", () => {
    const found = [{ severity: "LOW" as const }, { severity: "CRITICAL" as const }, { severity: undefined }, { severity: "MEDIUM" as const }, { severity: "HIGH" as const }];
    expect(sortBySeverity(found).map(f => f.severity ?? "UNKNOWN")).toEqual(["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]);
    expect(["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"].map(s => severityTone(s as never))).toEqual(["err", "err", "warn", "muted", "muted"]);
    expect(severityCounts(found)).toEqual([
      { severity: "CRITICAL", count: 1 }, { severity: "HIGH", count: 1 }, { severity: "MEDIUM", count: 1 }, { severity: "LOW", count: 1 }, { severity: "UNKNOWN", count: 1 },
    ]);
    expect(severityCounts([])).toEqual([]);
  });

  test("test_843_the_licence_verdict_and_the_decision_get_a_tone_and_the_refusal_a_sentence", () => {
    expect(verdictTone("ALLOWED")).toBe("ok");
    expect(verdictTone("VENDORED")).toBe("ok");
    expect(verdictTone("REFUSED")).toBe("err");
    expect(verdictTone("UNKNOWN")).toBe("warn");
    expect(refusalText("LICENCE")).toMatch(/allowlist/);
    expect(refusalText("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  test("test_843_health_says_what_the_host_did_not_report", () => {
    expect(healthLine({ last_commit_at: "2026-05-01T10:00:00Z", archived: true })).toEqual(["Last commit 2026-05-01", "last release not reported", "archived"]);
    expect(healthLine({ archived: false })).toContain("not archived");
    expect(healthLine({})).toEqual(["Last commit not reported", "last release not reported"]);
  });

  test("test_843_a_report_blocks_on_a_refusal_a_refused_licence_a_high_finding_or_malware", () => {
    expect(reportBlocks(report())).toBe(false);
    expect(reportBlocks(report({ decision: "REFUSED" }))).toBe(true);
    expect(reportBlocks(report({ licence: { spdx: "GPL-3.0", source: "LICENSE", verdict: "REFUSED" } }))).toBe(true);
    expect(reportBlocks(report({ dependency_licences: [{ ecosystem: "npm", name: "x", version: "1", verdict: "REFUSED" }] }))).toBe(true);
    expect(reportBlocks(report({ vulnerabilities: [{ ecosystem: "PyPI", package: "p", version: "1", id: "CVE-1", severity: "HIGH" }] }))).toBe(true);
    expect(reportBlocks(report({ vulnerabilities: [{ ecosystem: "PyPI", package: "p", version: "1", id: "CVE-2", severity: "MEDIUM" }] }))).toBe(false); // reported, never blocks
    expect(reportBlocks(report({ clam_findings: [{ path: "x", signature: "Eicar" }] }))).toBe(true);
  });

  test("test_843_totals_count_repositories_reports_patches_and_components_and_a_missing_report_needs_attention", () => {
    const repos = [
      repo({ report: report(), patches: [{ path: "patches/p", sha256: "1".repeat(64), reason: "r", files: ["a"] }], sbom: [{ kind: "repository", name: "acme/lib", version: COMMIT }] }),
      repo({ url: "https://github.com/acme/other", report: null }),
    ];
    expect(totals(repos)).toEqual({ repositories: 2, withReport: 1, blocking: 1, patches: 1, components: 1 });
    expect(totals([])).toEqual({ repositories: 0, withReport: 0, blocking: 0, patches: 0, components: 0 });
  });
});
