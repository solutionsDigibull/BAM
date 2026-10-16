// Knowledge Explorer (E5.5): the graph the canvas shows, kept as plain data. Pages from the API are
// cleaned (missing arrays, edges to unknown nodes), merged without duplicates and capped; layout is
// dagre's (run in a web worker by `layout-client.ts`).
import dagre from "@dagrejs/dagre";

import type { GraphApiEdge, GraphApiNode, KnowledgeGraphPage } from "@/contracts";

export const MAX_NODES = 2000; // the canvas stays smooth up to here; more needs 'Load more'
export const LABEL_MAX = 200;
export const PAGE_LIMIT = 500;
export const NODE_W = 180;
export const NODE_H = 44;

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

/** Left-to-right dagre layout. Pure, so the web worker and the tests run the same code. */
export function layout(graph: Graph): Positions {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) g.setNode(node.id, { width: NODE_W, height: NODE_H });
  for (const edge of graph.edges) g.setEdge(edge.from, edge.to);
  dagre.layout(g);
  const out: Positions = {};
  for (const node of graph.nodes) {
    const p = g.node(node.id) as { x: number; y: number } | undefined;
    out[node.id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
  return out;
}
