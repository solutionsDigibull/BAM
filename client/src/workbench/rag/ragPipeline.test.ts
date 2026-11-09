import { describe, expect, test } from "vitest";

import { ragPipeline, relations, setRagState, stepsOf } from "./ragPipeline";

const counts = (chunks = 0, entities = 0, rel = 0) => ({ documents: 1, chunks, entities, relations: rel });

describe("the knowledge steps derived from a backend version (#711)", () => {
  test("test_711_no_version_means_every_step_is_queued", () => {
    expect(stepsOf(undefined, counts()).map((s) => s.status)).toEqual(["queued", "queued", "queued"]);
  });

  test("test_711_a_ready_version_has_every_step_done", () => {
    expect(stepsOf("READY", counts(5, 4, 3)).map((s) => s.status)).toEqual(["done", "done", "done"]);
  });

  test("test_711_a_building_version_runs_the_first_unreached_step", () => {
    expect(stepsOf("BUILDING", counts(5, 0, 0)).map((s) => s.status)).toEqual(["done", "running", "queued"]);
  });

  test("test_711_a_failed_version_fails_the_first_unreached_step_with_its_reason", () => {
    const steps = stepsOf("FAILED", counts(5, 0, 0), "extraction crashed");
    expect(steps.map((s) => s.status)).toEqual(["done", "failed", "queued"]);
    expect(steps[1]?.error).toBe("extraction crashed");
  });

  test("test_711_relations_come_only_from_the_graph_the_backend_returned", () => {
    expect(relations({})).toEqual([]);
    const graphData = {
      nodes: [
        { id: "a", label: "Acme", type: "Customer" },
        { id: "b", label: "RFQ-1", type: "Request" },
      ],
      edges: [
        { source: "a", target: "b", label: "submitted" },
        { source: "a", target: "gone", label: "dangling" },
      ],
    };
    expect(relations({ graphData })).toEqual([
      { from: "Acme", fromType: "Customer", label: "submitted", to: "RFQ-1", toType: "Request" },
    ]);
  });

  test("test_711_the_store_offers_no_action_until_the_host_provides_one", () => {
    setRagState({ loaded: true });
    expect(ragPipeline.canStart()).toBe(false);
  });
});
