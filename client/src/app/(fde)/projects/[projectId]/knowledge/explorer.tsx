"use client";

// Knowledge Explorer (E5.5, TD §9): a read-only look at a knowledge version's graph. Search, type filter,
// expand a node's neighbours, open its source passages. Nothing here changes the graph: no connecting,
// no reconnecting, no deleting, and no mutation request exists on this screen. Text from documents is
// rendered as text only (never as HTML). At most 2,000 nodes; more pages need an explicit 'Load more'.
import "@xyflow/react/dist/style.css";

import { useCustom } from "@refinedev/core";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Flex,
  Input,
  List,
  Segmented,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { KnowledgeGraphPage, KnowledgeNodeDetail } from "@/contracts";
import { dataProvider, type ApiError } from "@/providers/data-provider";

import {
  EMPTY_GRAPH,
  HIDE_ISOLATED_ABOVE,
  MAX_NODES,
  NEUTRAL,
  PAGE_LIMIT,
  cleanPage,
  degrees,
  expand,
  label,
  merge,
  neighbourhood,
  radius,
  typeColors,
  typesOf,
  withoutIsolated,
  type Graph,
  type Positions,
} from "./graph-model";
import { graphPath, nodePath } from "./knowledge-api";
import { layoutAsync } from "./layout-client";

export const EMPTY_GRAPH_MESSAGE = "This knowledge version has no entities yet.";
export const SEARCH_DEBOUNCE_MS = 300;

type Extra = { page: KnowledgeGraphPage | undefined; graph: Graph; cursor?: string | null };

type Props = {
  projectId: string;
  kvId: string;
  /** Set when a newer READY version exists than the one shown (it became OUTDATED while open). */
  newer?: { id: string; version: number } | null;
  onSwitch?: (kvId: string) => void;
};

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

const query = (search: string, types: string[], cursor?: string | null) => {
  const params: Record<string, string> = { limit: String(PAGE_LIMIT) };
  const q = search.trim().slice(0, 200);
  if (q) params.q = q;
  if (types.length) params.type = types.slice(0, 10).join(",");
  if (cursor) params.cursor = cursor;
  return params;
};

type Bubble = {
  label: string;
  type: string;
  color: string;
  r: number;
  degree: number;
  /** Names are shown for hubs, hits and the selection; the rest appear on hover (less clutter). */
  named: boolean;
  dim: boolean;
  hit: boolean;
  selected: boolean;
};

const HIDDEN_HANDLE = { opacity: 0, top: "50%", left: "50%", pointerEvents: "none" } as const;

/** One entity: a circle sized by its relations, coloured by type, named when it matters. */
function EntityBubble({ data }: NodeProps<Node<Bubble>>) {
  const { r } = data;
  return (
    <div
      className={`kx-node${data.named ? " kx-named" : ""}`}
      title={`${data.label} · ${data.type} · ${data.degree} relations`}
      style={{ width: r * 2, height: r * 2, opacity: data.dim ? 0.18 : 1 }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} isConnectable={false} />
      <div
        style={{
          width: "100%",
          height: "100%",
          borderRadius: "50%",
          background: data.color,
          border: data.selected
            ? "3px solid #1f1f1f"
            : data.hit
              ? "3px solid #faad14"
              : "2px solid #fff",
          boxShadow: "0 0 0 1px rgba(0,0,0,0.15)",
        }}
      />
      <span
        style={{
          position: "absolute",
          top: r * 2 + 3,
          left: "50%",
          transform: "translateX(-50%)",
          whiteSpace: "nowrap",
          fontSize: 11,
          lineHeight: "13px",
          maxWidth: 140,
          overflow: "hidden",
          textOverflow: "ellipsis",
          padding: "0 3px",
          borderRadius: 3,
          background: "rgba(255,255,255,0.85)",
          opacity: data.named ? 1 : 0, // present for screen readers and tests, shown on hover
        }}
        className="kx-label"
      >
        {data.label}
      </span>
      <Handle
        type="source"
        position={Position.Bottom}
        style={HIDDEN_HANDLE}
        isConnectable={false}
      />
    </div>
  );
}

const NODE_TYPES = { entity: EntityBubble };
const NAMED_HUBS = 12; // how many of the most connected entities are always named

export function Explorer({ projectId, kvId, newer, onSwitch }: Props) {
  const { message } = App.useApp();
  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const debounced = useDebounced(search, SEARCH_DEBOUNCE_MS);
  const [positions, setPositions] = useState<Positions>({});
  const [laidOut, setLaidOut] = useState(0); // remounts the canvas so it fits the new layout
  const [selected, setSelected] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [seenTypes, setSeenTypes] = useState<string[]>([]);
  const [view, setView] = useState<"graph" | "table">("graph");
  const [showIsolated, setShowIsolated] = useState<boolean | undefined>(undefined);

  const first = useCustom<KnowledgeGraphPage, ApiError>({
    url: graphPath(projectId, kvId),
    method: "get",
    config: { query: query(debounced, types) },
  });
  const detail = useCustom<KnowledgeNodeDetail, ApiError>({
    url: selected ? nodePath(projectId, kvId, selected) : "",
    method: "get",
    queryOptions: { enabled: Boolean(selected) },
  });

  // What is shown = the first page (version, search or filter) + what was loaded or expanded on top
  // of that page. A new first page drops the extras: they belong to the page they were added to.
  const page = first.result.data;
  const [extra, setExtra] = useState<Extra>({ page: undefined, graph: EMPTY_GRAPH });
  const mine = useMemo<Extra>(
    () => (extra.page === page ? extra : { page, graph: EMPTY_GRAPH, cursor: undefined }),
    [extra, page],
  );
  const graph = useMemo(() => merge(cleanPage(page), mine.graph), [page, mine.graph]);
  const cursor = mine.cursor === undefined ? (page?.next_cursor ?? null) : mine.cursor;
  const fresh = typesOf(graph).filter((t) => !seenTypes.includes(t));
  if (fresh.length) setSeenTypes([...seenTypes, ...fresh].sort()); // the filter keeps every type seen

  // Progressive disclosure: a big graph starts with the connected part; the table lists the rest.
  const deg = useMemo(() => degrees(graph), [graph]);
  const isolated = graph.nodes.filter((n) => (deg.get(n.id) ?? 0) === 0).length;
  const wantIsolated = showIsolated ?? graph.nodes.length <= HIDE_ISOLATED_ABOVE;
  const drawn = useMemo(
    () => (wantIsolated || isolated === graph.nodes.length ? graph : withoutIsolated(graph)),
    [graph, wantIsolated, isolated],
  );
  const colors = useMemo(() => typeColors(seenTypes), [seenTypes]);

  useEffect(() => {
    let live = true;
    void layoutAsync(drawn).then((p) => {
      if (!live) return;
      setPositions(p);
      setLaidOut((n) => n + 1);
    });
    return () => {
      live = false;
    };
  }, [drawn]);

  const loadMore = useCallback(async () => {
    if (!cursor || graph.nodes.length >= MAX_NODES) return;
    setLoadingMore(true);
    try {
      if (!dataProvider.custom) return;
      const res = await dataProvider.custom<KnowledgeGraphPage>({
        url: graphPath(projectId, kvId),
        method: "get",
        query: query(debounced, types, cursor),
      });
      setExtra({
        page,
        graph: merge(mine.graph, cleanPage(res.data)),
        cursor: res.data?.next_cursor ?? null,
      });
    } catch {
      void message.error("Could not load more of the graph. Try again."); // keep what is shown
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, graph.nodes.length, projectId, kvId, debounced, types, message, page, mine]);

  const onExpand = () => {
    const d = detail.result.data;
    if (!d?.node) return;
    setExtra({ ...mine, page, graph: expand(graph, d.node.id, d.neighbours ?? []) });
  };

  const needle = debounced.trim().toLowerCase();
  const isHit = useCallback(
    (name: string) => Boolean(needle) && name.toLowerCase().includes(needle),
    [needle],
  );
  const lit = useMemo(() => (selected ? neighbourhood(drawn, selected) : null), [drawn, selected]);
  const hubs = useMemo(() => {
    const ranked = [...deg.entries()].sort((x, y) => y[1] - x[1]).slice(0, NAMED_HUBS);
    return new Set(ranked.filter(([, d]) => d > 0).map(([id]) => id));
  }, [deg]);
  const nodes: Node<Bubble>[] = useMemo(
    () =>
      drawn.nodes.map((n) => {
        const degree = deg.get(n.id) ?? 0;
        const hit = isHit(n.name);
        const r = radius(degree);
        return {
          id: n.id,
          type: "entity",
          position: positions[n.id] ?? { x: 0, y: 0 },
          width: r * 2,
          height: r * 2,
          data: {
            label: label(n.name || n.id),
            type: n.type,
            color: colors[n.type] ?? NEUTRAL,
            r,
            degree,
            named: hubs.has(n.id) || hit || n.id === selected || drawn.nodes.length <= 25,
            dim: lit !== null && !lit.has(n.id),
            hit,
            selected: n.id === selected,
          },
          className: hit ? "kx-hit" : undefined, // search hits (the e2e and styling hook)
          ariaLabel: `${n.type}: ${n.name}, ${degree} relations`,
        };
      }),
    [drawn.nodes, positions, deg, colors, hubs, selected, lit, isHit],
  );
  const edges: Edge[] = useMemo(
    () =>
      drawn.edges.map((e) => {
        const on = selected !== null && (e.from === selected || e.to === selected);
        return {
          id: e.id,
          source: e.from,
          target: e.to,
          type: "straight",
          // Relation names only on the selection: all of them at once is noise.
          label: on ? e.type.replaceAll("_", " ") : undefined,
          style: {
            stroke: on ? "#1f1f1f" : "#bfbfbf",
            strokeWidth: on ? 2 : 1,
            opacity: lit !== null ? (on ? 1 : 0.08) : 0.55,
          },
        };
      }),
    [drawn.edges, selected, lit],
  );
  const rows = useMemo(
    () =>
      graph.nodes
        .map((n) => ({
          key: n.id,
          name: n.name || n.id,
          type: n.type,
          relations: deg.get(n.id) ?? 0,
        }))
        .sort((x, y) => y.relations - x.relations || x.name.localeCompare(y.name)),
    [graph.nodes, deg],
  );

  if (first.query.isLoading && !page) return <LoadingState />;
  if (first.query.error)
    return <ApiErrorState error={first.query.error} onRetry={() => void first.query.refetch()} />;

  const capped = graph.nodes.length >= MAX_NODES;
  // With no node selected Refine still hands back a placeholder object: only a real detail counts.
  const d = selected && detail.result.data?.node ? detail.result.data : undefined;

  return (
    <Card title="Explorer">
      {newer ? (
        <Alert
          style={{ marginBottom: 12 }}
          type="info"
          showIcon
          message={`A newer knowledge version (v${newer.version}) is ready; this one is outdated.`}
          action={
            <Button size="small" onClick={() => onSwitch?.(newer.id)}>
              Switch to latest
            </Button>
          }
        />
      ) : null}
      <Flex gap={8} wrap style={{ marginBottom: 12 }}>
        <Input.Search
          aria-label="Search the graph"
          placeholder="Search entities"
          maxLength={200}
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <Select
          aria-label="Filter by type"
          mode="multiple"
          allowClear
          placeholder="All types"
          maxCount={10}
          style={{ minWidth: 220 }}
          value={types}
          onChange={setTypes}
          options={seenTypes.map((t) => ({ value: t, label: t }))}
        />
        <Typography.Text type="secondary" style={{ alignSelf: "center" }}>
          {graph.nodes.length} of {page?.total_estimate ?? 0} entities
        </Typography.Text>
        {isolated > 0 && isolated < graph.nodes.length ? (
          <Flex gap={6} align="center">
            <Switch size="small" checked={wantIsolated} onChange={setShowIsolated} />
            <Typography.Text type="secondary">Unconnected ({isolated})</Typography.Text>
          </Flex>
        ) : null}
        <Segmented
          aria-label="View"
          value={view}
          onChange={(v) => setView(v as "graph" | "table")}
          options={[
            { value: "graph", label: "Graph" },
            { value: "table", label: "Table" },
          ]}
        />
        {cursor && !capped ? (
          <Button loading={loadingMore} onClick={() => void loadMore()}>
            Load more
          </Button>
        ) : null}
        {capped && cursor ? (
          <Typography.Text type="warning" style={{ alignSelf: "center" }}>
            Showing the first {MAX_NODES} entities; narrow the search to see others.
          </Typography.Text>
        ) : null}
      </Flex>

      {graph.nodes.length === 0 ? (
        <EmptyState
          message={EMPTY_GRAPH_MESSAGE}
          action={
            <Link href={`/projects/${projectId}/documents`}>
              <Button>Go to documents</Button>
            </Link>
          }
        />
      ) : view === "table" ? (
        <Table
          size="small"
          pagination={{ pageSize: 25, hideOnSinglePage: true }}
          dataSource={rows}
          onRow={(r) => ({ onClick: () => setSelected(r.key), style: { cursor: "pointer" } })}
          columns={[
            { title: "Entity", dataIndex: "name", ellipsis: true },
            {
              title: "Type",
              dataIndex: "type",
              width: 160,
              render: (t: string) => <Tag color={colors[t] ?? NEUTRAL}>{t}</Tag>,
            },
            {
              title: "Relations",
              dataIndex: "relations",
              width: 110,
              sorter: (x, y) => x.relations - y.relations,
              defaultSortOrder: "descend",
            },
          ]}
        />
      ) : (
        <>
          <Flex gap={16} wrap align="center" style={{ marginBottom: 8 }}>
            {seenTypes.map((t) => (
              <Flex key={t} gap={6} align="center">
                <span
                  aria-hidden
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: colors[t] ?? NEUTRAL,
                    display: "inline-block",
                  }}
                />
                <Typography.Text type="secondary">{t}</Typography.Text>
              </Flex>
            ))}
            <Typography.Text type="secondary">
              Bigger circle = more relations. Click one to light up its neighbours.
            </Typography.Text>
          </Flex>
          <div style={{ height: 560 }} data-testid="knowledge-canvas" className="kx-canvas">
            <style>{`.kx-node:hover .kx-label{opacity:1 !important;z-index:5}`}</style>
            <ReactFlow
              key={laidOut}
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              nodesConnectable={false}
              edgesReconnectable={false}
              nodesDraggable
              deleteKeyCode={null}
              selectionKeyCode={null}
              multiSelectionKeyCode={null}
              zoomOnDoubleClick={false}
              onNodeClick={(_, node) => setSelected(node.id)}
              onPaneClick={() => setSelected(null)}
              onlyRenderVisibleElements
              minZoom={0.2}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </>
      )}

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={420}
        title={d ? <Tooltip title={d.node.name}>{label(d.node.name)}</Tooltip> : "Entity"}
      >
        {detail.query.isLoading ? (
          <LoadingState />
        ) : detail.query.error ? (
          <ApiErrorState error={detail.query.error} onRetry={() => void detail.query.refetch()} />
        ) : d ? (
          <Flex vertical gap={16}>
            <div>
              <Tag>{d.node.type}</Tag>
              <Typography.Text type="secondary">{d.node.degree ?? 0} relations</Typography.Text>
            </div>
            <div>
              <Flex justify="space-between" align="center">
                <Typography.Title level={5} style={{ margin: 0 }}>
                  Neighbours
                </Typography.Title>
                <Button size="small" onClick={onExpand} disabled={!(d.neighbours ?? []).length}>
                  Expand
                </Button>
              </Flex>
              <List
                size="small"
                dataSource={d.neighbours ?? []}
                renderItem={(n) => (
                  <List.Item>
                    <Typography.Text ellipsis={{ tooltip: n.name }}>{n.name}</Typography.Text>
                    <Tag>{n.type}</Tag>
                  </List.Item>
                )}
              />
              {d.has_more ? (
                <Typography.Text type="secondary">More neighbours exist.</Typography.Text>
              ) : null}
            </div>
            <div>
              <Typography.Title level={5}>Source passages</Typography.Title>
              {(d.source_passages ?? []).map((p, i) => (
                <Card
                  key={`${p.document_version_id}-${i}`}
                  size="small"
                  style={{ marginBottom: 8 }}
                >
                  <Flex justify="space-between" align="center">
                    <Typography.Text strong>{p.title || "Document"}</Typography.Text>
                    <Link href={`/projects/${projectId}/documents`}>Open source</Link>
                  </Flex>
                  <Typography.Text type="secondary">
                    {[
                      p.location?.page != null ? `page ${p.location.page}` : null,
                      p.location?.sheet ? `sheet ${p.location.sheet}` : null,
                      p.location?.cell ? `cell ${p.location.cell}` : null,
                      p.location?.line != null ? `line ${p.location.line}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "whole document"}
                  </Typography.Text>
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
                    {p.text}
                    {p.truncated ? "…" : ""}
                  </Typography.Paragraph>
                </Card>
              ))}
            </div>
          </Flex>
        ) : null}
      </Drawer>
    </Card>
  );
}
