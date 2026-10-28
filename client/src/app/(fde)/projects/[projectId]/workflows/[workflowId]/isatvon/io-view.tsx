"use client";

// The I/O dependency view (E8.8, TD §9.3): one Xyflow canvas per Macro Skill. Nodes are its
// Sub-Skills, edges run from an output field to the Sub-Skill that needs it as an input (labelled
// with the field), and edges inside a cycle are red. Click a node to open its drawer.
// Client-only (dynamic import, ssr: false).

import "@xyflow/react/dist/style.css";

import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { Alert, Card, Flex, Typography } from "antd";
import { useMemo } from "react";

import { EmptyState, Meta } from "@/components/ui-kit";
import type { WorkbenchMacro } from "@/contracts";

import { NODE_H, NODE_W, ioGraph, ioLayout } from "./io-graph";

export const CYCLE_COLOR = "#cf1322";

function MacroGraph({ macro, onOpen }: { macro: WorkbenchMacro; onOpen: (id: string) => void }) {
  const graph = useMemo(() => ioGraph(macro.sub_skills ?? []), [macro]);
  const positions = useMemo(() => ioLayout(graph), [graph]);
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.id,
    position: positions[n.id] ?? { x: 0, y: 0 },
    data: { label: n.label },
    style: { width: NODE_W, height: NODE_H, fontSize: 12 },
  }));
  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.field,
    className: e.cycle ? "io-edge-cycle" : undefined,
    animated: e.cycle,
    style: e.cycle ? { stroke: CYCLE_COLOR, strokeWidth: 2 } : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, color: e.cycle ? CYCLE_COLOR : undefined },
  }));
  return (
    <Card
      size="small"
      title={`${macro.macro_category} — ${macro.name}`}
      data-testid={`io-macro-${macro.id}`}
    >
      {graph.cycles.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 8 }}
          data-testid="io-cycle"
          message={`${graph.cycles.length} dependency cycle${graph.cycles.length === 1 ? "" : "s"}: the red edges form a loop`}
        />
      )}
      {graph.nodes.length === 0 ? (
        <EmptyState message="No Sub-Skills in this Macro Skill" />
      ) : (
        <div style={{ height: 260 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            fitView
            nodesConnectable={false}
            nodesDraggable={false}
            onNodeClick={(_, n) => onOpen(n.id)}
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
      <Meta>
        {graph.edges.length} link{graph.edges.length === 1 ? "" : "s"} between {graph.nodes.length}{" "}
        Sub-Skill{graph.nodes.length === 1 ? "" : "s"}
      </Meta>
    </Card>
  );
}

export default function IoView({
  macros,
  onOpen,
}: {
  macros: WorkbenchMacro[];
  onOpen: (subSkillId: string) => void;
}) {
  return (
    <Flex vertical gap={12} data-testid="io-view">
      <Typography.Text type="secondary">
        An arrow runs from the Sub-Skill that outputs a field to the one that needs it as an input,
        within each Macro Skill.
      </Typography.Text>
      {macros.map((m) => (
        <MacroGraph key={m.id} macro={m} onOpen={onOpen} />
      ))}
    </Flex>
  );
}
