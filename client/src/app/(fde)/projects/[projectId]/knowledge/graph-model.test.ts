import { describe, expect, test } from "vitest";

import type { GraphApiEdge, GraphApiNode } from "@/contracts";

import {
  EMPTY_GRAPH,
  LABEL_MAX,
  MAX_NODES,
  NEUTRAL,
  cleanPage,
  degrees,
  expand,
  label,
  layout,
  merge,
  neighbourhood,
  radius,
  typeColors,
  typesOf,
  withoutIsolated,
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

  test("layout places every node, the same way every time, without overlaps", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => node(`n${i}`));
    const edges = nodes.slice(1, 12).map((n) => edge("n0", n.id)); // a hub and 19 loners
    const g = merge(EMPTY_GRAPH, { nodes, edges });
    const p = layout(g);
    expect(Object.keys(p)).toHaveLength(30);
    expect(layout(g)).toEqual(p); // deterministic: a re-render never shuffles the picture
    const deg = degrees(g);
    const centres = nodes.map((n) => {
      const r = radius(deg.get(n.id) ?? 0);
      return { x: p[n.id]!.x + r, y: p[n.id]!.y + r, r };
    });
    for (const [i, a] of centres.entries()) {
      for (const b of centres.slice(i + 1)) {
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(a.r + b.r);
      }
    }
  });

  test("layout copes with an empty graph, self-loops and duplicate edges", () => {
    expect(layout(EMPTY_GRAPH)).toEqual({});
    const g = merge(EMPTY_GRAPH, {
      nodes: [node("a"), node("b")],
      edges: [edge("a", "a"), { ...edge("a", "b"), id: "x" }, { ...edge("a", "b"), id: "y" }],
    });
    expect(Object.keys(layout(g)).sort()).toEqual(["a", "b"]);
    expect(degrees(g).get("a")).toBe(2); // the self-loop does not count
  });

  test("hubs are bigger, unconnected nodes can be left out, a selection lights its neighbours", () => {
    expect(radius(9)).toBeGreaterThan(radius(1));
    const g = merge(EMPTY_GRAPH, {
      nodes: [node("hub"), node("a"), node("b"), node("alone")],
      edges: [edge("hub", "a"), edge("hub", "b")],
    });
    expect(withoutIsolated(g).nodes.map((n) => n.id)).toEqual(["hub", "a", "b"]);
    expect([...neighbourhood(g, "a")].sort()).toEqual(["a", "hub"]);
    expect([...neighbourhood(g, "alone")]).toEqual(["alone"]);
  });

  test("each type gets its own colour, the catch-all type and overflow get grey", () => {
    const c = typeColors(["Part", "Company", "entity", "A", "B", "C", "D", "E"]);
    expect(c.entity).toBe(NEUTRAL);
    expect(new Set(Object.values(c).filter((v) => v !== NEUTRAL)).size).toBe(
      Object.values(c).filter((v) => v !== NEUTRAL).length,
    );
    expect(typeColors(["Part", "Company"])).toEqual(typeColors(["Company", "Part"])); // stable
  });

  test("layout of the largest allowed graph stays within a few seconds and finite", () => {
    const nodes = Array.from({ length: MAX_NODES }, (_, i) => node(`n${i}`));
    const edges = nodes.slice(1).map((n, i) => edge(`n${i % 40}`, n.id)); // 40 hubs, a long tail
    const began = performance.now();
    const p = layout(merge(EMPTY_GRAPH, { nodes, edges }));
    expect(performance.now() - began).toBeLessThan(8000);
    expect(Object.values(p).every((v) => Number.isFinite(v.x) && Number.isFinite(v.y))).toBe(true);
  }, 30_000);
});
