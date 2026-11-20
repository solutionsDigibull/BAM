import { cleanup, render, screen, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test } from "vitest";

import type { KnowledgeVersionList, KnowledgeVersionSummary } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { ContextReadiness } from "./ContextReadiness";

const version = (over: Partial<KnowledgeVersionSummary> = {}) =>
  ({
    id: "kv2", version: 2, status: "READY", created_at: "2026-11-20T08:00:00Z", finished_at: "2026-11-20T08:06:00Z",
    okf_sha256: "ab".repeat(32),
    counts: { documents: 2, entities: 313, relations: 540, chunks: 120 },
    coverage: { documents: [], masters_included: 12, master_documents: 0, entities: 313, relations: 540, chunks: 120, extraction: "openrouter|gemini|embed-3-small|2", anchors: { total: 10, found: 8, missing: ["853-A92173", "IIS00196"] } },
    documents: [
      { document_version_id: "d1", title: "PFD 078.xlsx", status: "COVERED", chunks: 90, entities: 200 },
      { document_version_id: "d2", title: "8D Havells.xlsx", status: "FAILED", chunks: 0, entities: 0, reason: "PARSE_ERROR" },
    ],
    ...over,
  }) as unknown as KnowledgeVersionSummary;
const list = (...v: KnowledgeVersionSummary[]) => ({ versions: v }) as KnowledgeVersionList;
const problem = (statusCode: number, detail = "x") => Object.assign(new Error(detail), { statusCode, problem: { detail } });

const install = (api: Partial<BamApi>) => setBamApi(api as unknown as BamApi);
const wrap = () =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <ContextReadiness />
      </Refine>
    </div>,
  );
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("Context readiness (2.4, #825): the newest knowledge version", () => {
  test("test_825_ready_version_shows_graph_retrieval_coverage_and_bundle", async () => {
    install({ getKnowledgeVersions: async () => list(version({ version: 1, id: "kv1", okf_sha256: null } as Partial<KnowledgeVersionSummary>), version()) });
    wrap();
    expect(await screen.findByText("313")).toBeTruthy();
    expect(screen.getByText("8 / 10")).toBeTruthy();
    expect(screen.getByText(/853-A92173, IIS00196/)).toBeTruthy();
    const table = screen.getByRole("table", { name: "Document coverage" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(table.textContent).toContain("FAILED · PARSE_ERROR");
    expect(screen.getByText("1 of 2 covered")).toBeTruthy();
    expect(screen.getByText(/version 2/)).toBeTruthy(); // the newest, not the first in the list
  });

  test("test_825_building_and_failed_versions_say_so", async () => {
    install({ getKnowledgeVersions: async () => list(version({ status: "BUILDING" })) });
    wrap();
    expect(await screen.findByText(/Version 2 is building/)).toBeTruthy();
    cleanup();
    install({ getKnowledgeVersions: async () => list(version({ status: "FAILED", error_reason: "COGNEE_TIMEOUT" })) });
    wrap();
    expect(await screen.findByText(/Version 2 failed/)).toBeTruthy();
    expect(screen.getByText(/COGNEE_TIMEOUT/)).toBeTruthy();
  });

  test("test_777_a_credit_refusal_names_the_cause_and_what_to_do", async () => {
    install({ getKnowledgeVersions: async () => list(version({ status: "FAILED", error_reason: "ModelCreditExhausted" })) });
    wrap();
    expect(await screen.findByText(/The model account is out of credit/)).toBeTruthy();
    expect(screen.getByText(/add credit to the model provider account/)).toBeTruthy();
    cleanup();
    install({ getKnowledgeVersions: async () => list(version({ status: "FAILED", error_reason: "KnowledgeTokenCap" })) });
    wrap();
    expect(await screen.findByText(/reached its token limit/)).toBeTruthy();
    expect(screen.getByText(/raise the limit/)).toBeTruthy();
  });

  test("test_825_no_knowledge_yet_is_an_empty_state", async () => {
    install({ getKnowledgeVersions: async () => list() });
    wrap();
    expect(await screen.findByText(/No knowledge yet/)).toBeTruthy();
  });

  test("test_825_forbidden_and_error_states", async () => {
    install({ getKnowledgeVersions: async () => { throw problem(403); } });
    wrap();
    expect(await screen.findByText(/You can't see this project's knowledge/)).toBeTruthy();
    cleanup();
    install({ getKnowledgeVersions: async () => { throw problem(500, "storage down"); } });
    wrap();
    expect(await screen.findByText(/Could not load the knowledge/)).toBeTruthy();
    expect(screen.getByText(/storage down/)).toBeTruthy();
  });

  test("test_825_mock_extraction_is_named", async () => {
    const v = version();
    install({ getKnowledgeVersions: async () => list({ ...v, coverage: { ...v.coverage, extraction: "mock|mock|mock|1" } }) });
    wrap();
    expect(await screen.findByText(/Built with the mock model/)).toBeTruthy();
  });
});
