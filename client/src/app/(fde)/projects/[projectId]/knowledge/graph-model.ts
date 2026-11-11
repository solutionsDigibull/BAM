// Knowledge Explorer (E5.5): the graph the canvas shows, kept as plain data. Pages from the API are
// cleaned (missing arrays, edges to unknown nodes), merged without duplicates and capped. A knowledge
// graph has no top and no bottom, so the layout is a force simulation (clusters form where the
// relations are), run in a web worker by `layout-client.ts`.
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

import type { GraphApiEdge, GraphApiNode, KnowledgeGraphPage } from "@/contracts";

export const MAX_NODES = 2000; // the canvas stays smooth up to here; more needs 'Load more'
export const LABEL_MAX = 200;
export const PAGE_LIMIT = 500;

export type Graph = { nodes: GraphApiNode[]; edges: GraphApiEdge[] };
export type Positions = Record<string, { x: number; y: number }>;

export const EMPTY_GRAPH: Graph = { nodes: [], edges: [] };

/** A page as the API sent it, or as a broken proxy might: never trust the shape. */
export function cleanPage(page: Partial<KnowledgeGraphPage> | null | undefined): Graph {
  const nodes = Array.isArray(page?.nodes)
    ? page.nodes.filter((n): n is GraphApiNode => typeof n?.id === "string")
    : [];
  const edges = Array.isArray(page?.edges)
    ? page.edges.filter(
        (e): e is GraphApiEdge =>
          typeof e?.from === "string" && typeof e?.to === "string" && typeof e?.id === "string",
      )
    : [];
  return { nodes, edges };
}

/** `more` added to `graph`: nodes by id (first wins), at most MAX_NODES; edges whose both ends are
 *  shown (an edge to a node on a later page appears once that page is loaded). */
export function merge(graph: Graph, more: Graph, max = MAX_NODES): Graph {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  for (const node of more.nodes) {
    if (byId.size >= max) break;
    if (!byId.has(node.id)) byId.set(node.id, node);
  }
  const edges = new Map<string, GraphApiEdge>();
  for (const edge of [...graph.edges, ...more.edges]) {
    if (byId.has(edge.from) && byId.has(edge.to)) edges.set(edge.id, edge);
  }
  return { nodes: [...byId.values()], edges: [...edges.values()] };
}

/** Neighbours of `center` added as nodes, each linked to it (E5.5-T02: no duplicates). */
export function expand(graph: Graph, center: string, neighbours: GraphApiNode[]): Graph {
  const links: GraphApiEdge[] = neighbours.map((n) => ({
    id: `expand:${center}:${n.id}`,
    type: "related",
    from: center,
    to: n.id,
    external: false,
  }));
  const known = new Set(graph.edges.map((e) => [e.from, e.to].sort().join("|")));
  const fresh = links.filter((l) => !known.has([l.from, l.to].sort().join("|")));
  return merge(graph, { nodes: neighbours, edges: fresh });
}

export const label = (text: string): string =>
  text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text;

/** Distinct node types, for the type filter: only values the API sent. */
export const typesOf = (graph: Graph): string[] =>
  [...new Set(graph.nodes.map((n) => n.type))].sort();

/** Relations per node within `graph`: what the picture can show (the API's `degree` counts the
 *  whole graph, including nodes on pages not loaded). */
export function degrees(graph: Graph): Map<string, number> {
  const out = new Map<string, number>(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) {
    if (e.from === e.to) continue;
    out.set(e.from, (out.get(e.from) ?? 0) + 1);
    out.set(e.to, (out.get(e.to) ?? 0) + 1);
  }
  return out;
}

/** Circle radius for a node: area grows with its relations, so hubs read at a glance. */
export const radius = (degree: number): number => Math.min(26, 6 + 3.2 * Math.sqrt(degree));

/** Above this many nodes the picture starts without the unconnected ones (progressive disclosure). */
export const HIDE_ISOLATED_ABOVE = 40;

/** `graph` without nodes that have no relation in it. They stay in the table view. */
export function withoutIsolated(graph: Graph): Graph {
  const deg = degrees(graph);
  return { nodes: graph.nodes.filter((n) => (deg.get(n.id) ?? 0) > 0), edges: graph.edges };
}

/** The node and everything one relation away: what stays lit when a node is selected. */
export function neighbourhood(graph: Graph, id: string): Set<string> {
  const near = new Set([id]);
  for (const e of graph.edges) {
    if (e.from === id) near.add(e.to);
    if (e.to === id) near.add(e.from);
  }
  return near;
}

// Colour-blind-safe qualitative palette (Okabe-Ito); one colour per type, in sorted order.
const PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#F0E442"];
export const NEUTRAL = "#8c8c8c"; // beyond the palette, and for the catch-all type

export function typeColors(types: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {};
  [...types].sort().forEach((t, i) => {
    out[t] = t.toLowerCase() === "entity" || i >= PALETTE.length ? NEUTRAL : (PALETTE[i] as string);
  });
  return out;
}

type Body = { id: string; r: number; x?: number; y?: number };

/** Force layout, fixed number of ticks so the same graph always lands in the same place. Positions
 *  are the top-left of each node's box (a circle of its radius), like React Flow wants. */
export function layout(graph: Graph): Positions {
  const deg = degrees(graph);
  const bodies: Body[] = graph.nodes.map((n) => ({ id: n.id, r: radius(deg.get(n.id) ?? 0) }));
  const links = graph.edges
    .filter((e) => e.from !== e.to)
    .map((e) => ({ source: e.from, target: e.to }));
  // A bigger graph needs longer links and a harder push apart, or it collapses into a hairball.
  const spread = 1 + Math.log10(Math.max(1, bodies.length) / 20);
  const sim = forceSimulation(bodies)
    .force(
      "link",
      forceLink<Body, { source: string; target: string }>(links)
        .id((b) => b.id)
        .distance(55 * spread)
        .strength(0.5),
    )
    .force(
      "charge",
      forceManyBody<Body>()
        .strength((b) => (-70 - b.r * 8) * spread)
        .distanceMax(500),
    )
    .force(
      "collide",
      forceCollide<Body>((b) => b.r + 10),
    )
    .force("x", forceX(0).strength(0.05)) // pulls separate clusters together instead of off-screen
    .force("y", forceY(0).strength(0.05))
    .stop();
  // Fewer ticks for a big graph: the cost grows with nodes x ticks, and a big one settles coarser anyway.
  const ticks = bodies.length <= 400 ? 300 : bodies.length <= 1000 ? 220 : 150;
  for (let i = 0; i < ticks; i++) sim.tick();
  const out: Positions = {};
  for (const b of bodies) out[b.id] = { x: (b.x ?? 0) - b.r, y: (b.y ?? 0) - b.r };
  return out;
}
