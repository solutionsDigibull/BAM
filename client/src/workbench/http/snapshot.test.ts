import { afterEach, describe, expect, test, vi } from "vitest";

import { ragPipeline } from "../rag/ragPipeline";
import { buildSnapshot, resetSnapshotCache } from "./snapshot";

const PROJECT = "11111111-1111-4111-8111-111111111111";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

/** Answers the reads the snapshot makes; any other request fails the test. */
function api(opts: { state: string; versions?: unknown[]; documents?: unknown[] }) {
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
