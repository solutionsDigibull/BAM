// The I/O dependency view's data (E8.8, TD §9.3): within one Macro Skill, an edge runs from the
// Sub-Skill whose outputs (O.json_schema fields + state_handoff_id) include a name to every other
// Sub-Skill that lists that name in S.required_inputs. Computed from the rows the server sent; a
// cycle is found by strongly connected components and highlighted. ponytail: no server endpoint
// yet: the build plan's DAG (E9.5, #105) will validate the same edges server-side.

import dagre from "@dagrejs/dagre";

import type { WorkbenchSubSkill } from "@/contracts";

import { outputFields } from "./gate-expr";

export type IoNode = { id: string; label: string; inputs: string[]; outputs: string[] };
export type IoEdge = { id: string; source: string; target: string; field: string; cycle: boolean };
export type IoGraph = { nodes: IoNode[]; edges: IoEdge[]; cycles: string[][] };

type Definition = {
  s_source?: { required_inputs?: string[] };
  o_outcome?: { json_schema?: Record<string, unknown> };
};

function node(row: WorkbenchSubSkill): IoNode {
  const sub = row.sub_skill as unknown as {
    skill_metadata?: { skill_id?: string };
    isatvon_definition?: Definition;
  };
  const definition = sub?.isatvon_definition ?? {};
  return {
    id: row.id,
    label: sub?.skill_metadata?.skill_id ?? row.id,
    inputs: definition.s_source?.required_inputs ?? [],
    outputs: [...outputFields(definition.o_outcome?.json_schema), "state_handoff_id"],
  };
}

/** Tarjan's strongly connected components; a component with 2+ nodes (or a self-loop) is a cycle. */
function components(ids: string[], next: Map<string, string[]>): string[][] {
  let index = 0;
  const order = new Map<string, number>();
  const low = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const found: string[][] = [];
  const visit = (v: string) => {
    order.set(v, index);
    low.set(v, index);
    index += 1;
    stack.push(v);
    onStack.add(v);
    for (const w of next.get(v) ?? []) {
      if (!order.has(w)) {
        visit(w);
        low.set(v, Math.min(low.get(v)!, low.get(w)!));
      } else if (onStack.has(w)) low.set(v, Math.min(low.get(v)!, order.get(w)!));
    }
    if (low.get(v) === order.get(v)) {
      const component: string[] = [];
      let w: string;
      do {
        w = stack.pop()!;
        onStack.delete(w);
        component.push(w);
      } while (w !== v);
      found.push(component.reverse());
    }
  };
  for (const id of ids) if (!order.has(id)) visit(id);
  return found;
}

/** Nodes, field edges and cycles for one Macro Skill's Sub-Skills (in their order). */
export function ioGraph(rows: WorkbenchSubSkill[]): IoGraph {
  const nodes = rows.map(node);
  const edges: Omit<IoEdge, "cycle">[] = [];
  for (const target of nodes)
    for (const field of target.inputs)
      for (const source of nodes)
        if (source.id !== target.id && source.outputs.includes(field))
          edges.push({
            id: `${source.id}>${target.id}:${field}`,
            source: source.id,
            target: target.id,
            field,
          });
  const next = new Map<string, string[]>();
  for (const e of edges) next.set(e.source, [...(next.get(e.source) ?? []), e.target]);
  const cycles = components(
    nodes.map((n) => n.id),
    next,
  ).filter((c) => c.length > 1);
  const inCycle = new Map<string, number>();
  cycles.forEach((c, i) => c.forEach((id) => inCycle.set(id, i)));
  return {
    nodes,
    edges: edges.map((e) => ({
      ...e,
      cycle: inCycle.has(e.source) && inCycle.get(e.source) === inCycle.get(e.target),
    })),
    cycles,
  };
}

/** "Dependency cycle: MS01.2 → MS01.3 → MS01.2", for the drawer of a Sub-Skill in a cycle. */
export function cycleMessage(graph: IoGraph, id: string): string | null {
  const cycle = graph.cycles.find((c) => c.includes(id));
  if (!cycle) return null;
  const label = (n: string) => graph.nodes.find((x) => x.id === n)?.label ?? n;
  return `Dependency cycle: ${[...cycle, cycle[0]!].map(label).join(" → ")}`;
}

export const NODE_W = 200;
export const NODE_H = 48;

/** Left-to-right dagre positions (top-left corners), as the Knowledge explorer lays out. */
export function ioLayout(graph: IoGraph): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph({ multigraph: true });
  g.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of graph.nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of graph.edges) g.setEdge(e.source, e.target, {}, e.id);
  dagre.layout(g);
  const out: Record<string, { x: number; y: number }> = {};
  for (const n of graph.nodes) {
    const p = g.node(n.id) as { x: number; y: number } | undefined;
    out[n.id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
  return out;
}
