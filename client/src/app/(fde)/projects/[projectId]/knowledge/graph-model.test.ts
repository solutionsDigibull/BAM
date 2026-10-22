import { describe, expect, test } from "vitest";

import type { GraphApiEdge, GraphApiNode } from "@/contracts";

import {
  EMPTY_GRAPH,
  LABEL_MAX,
  MAX_NODES,
  cleanPage,
  expand,
  label,
  layout,
  merge,
  typesOf,
} from "./graph-model";

const node = (id: string, type = "Concept"): GraphApiNode => ({
  id,
  type,
  name: `N ${id}`,
  degree: 0,
});
const edge = (from: string, to: string): GraphApiEdge => ({
  id: `${from}-${to}`,
  type: "related_to",
  from,
  to,
  external: false,
});

describe("Knowledge Explorer graph model (E5.5)", () => {
  test("e5_5_t06 malformed pages become an empty graph, edges to unknown nodes are dropped", () => {
    const broken = cleanPage({ nodes: null, edges: [{ from: "x", to: "y" }] } as never);
    expect(broken).toEqual(EMPTY_GRAPH);
    expect(merge(EMPTY_GRAPH, cleanPage({ nodes: [node("a")], edges: [edge("a", "zz")] }))).toEqual(
      {
        nodes: [node("a")],
        edges: [],
      },
    );
    expect(cleanPage(undefined)).toEqual(EMPTY_GRAPH);
  });

  test("e5_5_t02 expanding 12 neighbours adds them once, linked to the node", () => {
    const start = merge(EMPTY_GRAPH, {
      nodes: [node("hub"), node("n0")],
      edges: [edge("hub", "n0")],
    });
    const neighbours = Array.from({ length: 12 }, (_, i) => node(`n${i}`));
    const once = expand(start, "hub", neighbours);
    const twice = expand(once, "hub", neighbours);
    expect(once.nodes.map((n) => n.id).sort()).toEqual(
      ["hub", ...neighbours.map((n) => n.id)].sort(),
    );
    expect(new Set(once.nodes.map((n) => n.id)).size).toBe(13);
    expect(twice).toEqual(once);
    expect(once.edges.filter((e) => e.from === "hub" || e.to === "hub")).toHaveLength(12);
  });

  test("pages merge without duplicates and stop at the node cap", () => {
    const a = { nodes: [node("1"), node("2")], edges: [edge("1", "2")] };
    const b = { nodes: [node("2"), node("3")], edges: [edge("2", "3")] };
    expect(merge(merge(EMPTY_GRAPH, a), b).nodes.map((n) => n.id)).toEqual(["1", "2", "3"]);
    const many = {
      nodes: Array.from({ length: MAX_NODES + 50 }, (_, i) => node(`${i}`)),
      edges: [],
    };
    expect(merge(EMPTY_GRAPH, many).nodes).toHaveLength(MAX_NODES);
  });

  test("long labels get an ellipsis at 200 characters", () => {
    expect(label("x".repeat(500))).toHaveLength(LABEL_MAX);
    expect(label("x".repeat(500)).endsWith("…")).toBe(true);
    expect(label("short")).toBe("short");
  });

  test("types come only from the data", () => {
    expect(typesOf({ nodes: [node("a", "B"), node("b", "A"), node("c", "B")], edges: [] })).toEqual(
      ["A", "B"],
    );
  });

  test("layout places every node, left to right along edges", () => {
    const g = merge(EMPTY_GRAPH, { nodes: [node("a"), node("b")], edges: [edge("a", "b")] });
    const p = layout(g);
    expect(Object.keys(p).sort()).toEqual(["a", "b"]);
    expect(p.b!.x).toBeGreaterThan(p.a!.x);
  });
});
