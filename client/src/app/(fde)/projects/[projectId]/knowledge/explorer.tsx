"use client";

// Knowledge Explorer (E5.5, TD §9): a read-only look at a knowledge version's graph. Search, type filter,
// expand a node's neighbours, open its source passages. Nothing here changes the graph: no connecting,
// no reconnecting, no deleting, and no mutation request exists on this screen. Text from documents is
// rendered as text only (never as HTML). At most 2,000 nodes; more pages need an explicit 'Load more'.
import "@xyflow/react/dist/style.css";

import { useCustom } from "@refinedev/core";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Flex,
  Input,
  List,
  Select,
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
  MAX_NODES,
  NODE_H,
  NODE_W,
  PAGE_LIMIT,
  cleanPage,
  expand,
  label,
  merge,
  typesOf,
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

export function Explorer({ projectId, kvId, newer, onSwitch }: Props) {
  const { message } = App.useApp();
  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const debounced = useDebounced(search, SEARCH_DEBOUNCE_MS);
  const [positions, setPositions] = useState<Positions>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [seenTypes, setSeenTypes] = useState<string[]>([]);

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

  useEffect(() => {
    let live = true;
    void layoutAsync(graph).then((p) => {
      if (live) setPositions(p);
    });
    return () => {
      live = false;
    };
  }, [graph]);

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
  const nodes: Node[] = useMemo(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        position: positions[n.id] ?? { x: 0, y: 0 },
        data: { label: label(n.name || n.id) },
        style: {
          width: NODE_W,
          height: NODE_H,
          fontSize: 12,
          ...(isHit(n.name)
            ? { borderColor: "#faad14", borderWidth: 2, background: "#fffbe6" }
            : {}),
        },
        className: isHit(n.name) ? "kx-hit" : undefined,
        ariaLabel: `${n.type}: ${n.name}`,
      })),
    [graph.nodes, positions, isHit],
  );
  const edges: Edge[] = useMemo(
    () => graph.edges.map((e) => ({ id: e.id, source: e.from, target: e.to, label: e.type })),
    [graph.edges],
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
      ) : (
        <div style={{ height: 560 }} data-testid="knowledge-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodesConnectable={false}
            edgesReconnectable={false}
            nodesDraggable
            deleteKeyCode={null}
            selectionKeyCode={null}
            multiSelectionKeyCode={null}
            zoomOnDoubleClick={false}
            onNodeClick={(_, node) => setSelected(node.id)}
            onlyRenderVisibleElements
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
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
