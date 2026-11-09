import { afterEach, describe, expect, test, vi } from "vitest";

import { ragPipeline } from "../rag/ragPipeline";
import { buildSnapshot, resetSnapshotCache, sowOf } from "./snapshot";

const PROJECT = "11111111-1111-4111-8111-111111111111";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

/** Answers the reads the snapshot makes; any other request fails the test. */
function api(opts: { state: string; versions?: unknown[]; documents?: unknown[]; sow?: unknown }) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      const path = url.replace("/api/proxy/api/v1/", "");
      if (path.startsWith(`projects/${PROJECT}/documents`))
        return json({ items: opts.documents ?? [], next_cursor: null });
      if (path === `projects/${PROJECT}/pipeline`)
        return json({
          state: opts.state,
          steps: [],
          counts: { uploaded: 0, parsed: 0, rejected: 0, failed: 0 },
        });
      if (path === `projects/${PROJECT}/knowledge`) return json({ versions: opts.versions ?? [] });
      if (path === `projects/${PROJECT}/sow`) return json(opts.sow ?? { sow_id: "s1", current: null, approved: null, etag: "e" });
      if (path.includes("/graph"))
        return json({
          knowledge_version_id: "kv1",
          status: "READY",
          nodes: [{ id: "n1", type: "Customer", name: "Acme" }],
          edges: [],
          total_estimate: 1,
        });
      throw new Error(`unexpected request ${url}`);
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetSnapshotCache();
});

describe("the workbench snapshot composed from the API (#711)", () => {
  test("test_711_a_fresh_project_asks_for_uploads_and_locks_the_later_steps", async () => {
    api({ state: "IDLE" });
    const snap = await buildSnapshot(PROJECT);
    expect(snap.activeStep).toBe(1);
    expect(snap.steps[0]?.reason).toBe("Upload documents in Upload");
    expect(snap.steps.slice(1).every((s) => s.state === "locked")).toBe(true);
    expect(snap.ingestion).toBeUndefined();
    expect(snap.sowGeneration).toEqual([]);
  });

  test("test_711_a_running_pipeline_shows_a_run_and_the_step_as_active", async () => {
    api({ state: "KNOWLEDGE_BUILDING" });
    const snap = await buildSnapshot(PROJECT);
    expect(snap.ingestion).toBeDefined();
    expect(snap.steps[0]).toMatchObject({ state: "active", background: 1 });
  });

  test("test_711_a_failed_knowledge_build_fails_the_step_with_the_backends_reason", async () => {
    api({
      state: "KNOWLEDGE_FAILED",
      versions: [
        {
          id: "kv1",
          version: 1,
          status: "FAILED",
          coverage: {},
          error_reason: "no entities",
          created_at: "2026-11-09T10:00:00Z",
        },
      ],
    });
    const snap = await buildSnapshot(PROJECT);
    expect(snap.steps[0]).toMatchObject({ state: "failed", reason: "no entities" });
  });

  test("test_711_a_ready_version_feeds_the_knowledge_store_with_its_graph", async () => {
    const calls = api({
      state: "SOW_GENERATING",
      versions: [
        {
          id: "kv1",
          version: 2,
          status: "READY",
          counts: { documents: 3, entities: 9, relations: 4, chunks: 30 },
          coverage: { masters_included: 5, master_documents: 2 },
          created_at: "2026-11-09T10:00:00Z",
        },
      ],
    });
    const snap = await buildSnapshot(PROJECT);
    const rag = ragPipeline.getState();
    expect(rag).toMatchObject({ loaded: true, runId: "kv1", version: 2, status: "READY" });
    expect(rag.counts).toEqual({ documents: 3, entities: 9, relations: 4, chunks: 30 });
    expect(rag.masters).toEqual({ included: 5, documents: 2 });
    expect(rag.graphData?.nodes.map((n) => n.label)).toEqual(["Acme"]);
    expect(snap.sowGeneration[0]?.status).toBe("running");
    // the graph is fetched once per version
    await buildSnapshot(PROJECT);
    expect(calls.filter((c) => c.includes("/graph"))).toHaveLength(1);
  });

  test("test_711_an_api_error_rejects_the_snapshot_so_the_screen_shows_its_error_state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ title: "x", detail: "down" }), { status: 503 })),
    );
    await expect(buildSnapshot(PROJECT)).rejects.toThrow();
  });
});

const summary = (version: number, extra: Record<string, unknown> = {}) => ({
  id: `v${version}`,
  version,
  status: "IN_REVIEW",
  source: "GENERATED",
  sha256: "a".repeat(64),
  parent_version: null,
  knowledge_version_id: "kv1",
  author: null,
  created_at: "2026-11-09T10:00:00Z",
  ...extra,
});

describe("the SOW in the workbench snapshot (#713)", () => {
  test("test_713_a_draft_sow_in_review_is_a_draft_with_no_approval", () => {
    const sow = sowOf({ sow_id: "s1", current: summary(2), approved: null, etag: "e" } as never, "SOW_REVIEW");
    expect(sow).toMatchObject({ version: 2, status: "draft", approval: undefined });
    expect(sow?.meta.sowId).toBe("s1");
  });

  test("test_713_the_approved_current_version_is_approved_with_who_and_which_version", () => {
    const v = summary(3, { status: "APPROVED", author: "fde-a1" });
    const sow = sowOf({ sow_id: "s1", current: v, approved: v, etag: "e" } as never, "SOW_APPROVED");
    expect(sow?.status).toBe("approved");
    expect(sow?.approval).toMatchObject({ version: 3, by: "fde-a1" });
  });

  test("test_713_a_newer_draft_after_an_approval_is_a_draft_and_the_approval_stays_visible", () => {
    const sow = sowOf({ sow_id: "s1", current: summary(4), approved: summary(3, { status: "APPROVED" }), etag: "e" } as never, "SOW_REVIEW");
    expect(sow?.status).toBe("draft");
    expect(sow?.approval?.version).toBe(3);
  });

  test("test_713_a_sow_being_regenerated_says_so", () => {
    expect(sowOf({ sow_id: "s1", current: summary(2), approved: null, etag: "e" } as never, "SOW_GENERATING")?.status).toBe("regenerating");
  });

  test("test_713_no_current_version_means_no_sow", () => {
    expect(sowOf({ sow_id: "s1", current: null, approved: null, etag: "e" } as never, "SOW_GENERATING")).toBeUndefined();
    expect(sowOf(undefined, "SOW_REVIEW")).toBeUndefined();
  });

  test("test_713_the_sow_is_asked_for_only_once_the_pipeline_has_reached_it", async () => {
    const early = api({ state: "KNOWLEDGE_BUILDING" });
    await buildSnapshot(PROJECT);
    expect(early.some((c) => c.endsWith("/sow"))).toBe(false);
    vi.unstubAllGlobals();
    const late = api({ state: "SOW_REVIEW", sow: { sow_id: "s1", current: summary(1), approved: null, etag: "e" } });
    const snap = await buildSnapshot(PROJECT);
    expect(late.some((c) => c.endsWith("/sow"))).toBe(true);
    expect(snap.sow?.version).toBe(1);
  });

  test("test_713_a_sow_request_that_fails_does_not_break_the_snapshot", async () => {
    api({ state: "SOW_REVIEW", sow: undefined });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).endsWith("/sow") ? new Response("{}", { status: 500 }) : json({ items: [], next_cursor: null, state: "SOW_REVIEW", steps: [], counts: {}, versions: [] }))));
    const snap = await buildSnapshot(PROJECT);
    expect(snap.sow).toBeUndefined();
  });
});
