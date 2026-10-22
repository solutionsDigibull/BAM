"use client";

// The ISATVON 1.4 workbench (TD §9.3, E8.7): the Macro → Sub-Skill breakdown SBAM extracted from the
// approved SOW, in a grid whose columns come only from the published metadata (GET /schemas/isatvon/1.4).
// The breakdown endpoints are #94's; until they land this screen is built against sbam_contracts.

import { useCustom } from "@refinedev/core";
import { Alert, Button, Flex, Spin, Typography } from "antd";
import dynamic from "next/dynamic";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState, Meta, MUTED } from "@/components/ui-kit";
import type { IsatvonSchemaResponse, WorkbenchTree } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  isEditable,
  macroIds,
  metadataColumns,
  SCHEMA_URL,
  subSkills,
  toRows,
  treeUrl,
} from "./workbench-api";

const IsatvonGrid = dynamic(() => import("./isatvon-grid"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const EMPTY_MESSAGE = "No Macro Skills yet";
const EXTRACT_POLL_MS = 5_000;

export function WorkbenchScreen({
  projectId,
  workflowId,
}: {
  projectId: string;
  workflowId: string;
}) {
  const schema = useCustom<IsatvonSchemaResponse, ApiError>({ url: SCHEMA_URL, method: "get" });
  const treeQuery = useCustom<WorkbenchTree, ApiError>({
    url: treeUrl(projectId, workflowId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) =>
        q.state.data?.data?.workflow_state === "ISATVON_EXTRACTING" ? EXTRACT_POLL_MS : false,
    },
  });
  // `useCustom` hands back `{}` while loading and the API may send null: never trust the shape.
  const tree = treeQuery.result.data as unknown;
  const state = (tree as Partial<WorkbenchTree> | null)?.workflow_state;
  const groups = schema.result.data?.grid?.column_groups;
  const ids = useMemo(() => macroIds(tree), [tree]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set()); // all expanded at first
  const expanded = useMemo(() => new Set(ids.filter((id) => !collapsed.has(id))), [ids, collapsed]);
  const rows = useMemo(() => toRows(tree, expanded), [tree, expanded]);
  const subs = useMemo(() => subSkills(tree), [tree]);
  const invalid = subs.filter((s) => s.state === "INVALID").length;

  const toggle = (id: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  let body;
  if (schema.query.isLoading || treeQuery.query.isLoading) body = <LoadingState />;
  else if (schema.query.isError)
    body = <ApiErrorState error={schema.query.error} onRetry={() => schema.query.refetch()} />;
  else if (treeQuery.query.isError)
    body = (
      <ApiErrorState error={treeQuery.query.error} onRetry={() => treeQuery.query.refetch()} />
    );
  else if (state === "ISATVON_EXTRACTING")
    body = (
      <Flex vertical align="center" gap={12} style={{ padding: 48 }}>
        <Spin />
        <Typography.Text>
          Extracting the ISATVON 1.4 breakdown from the approved SOW…
        </Typography.Text>
        <Meta>This page updates on its own.</Meta>
      </Flex>
    );
  else if (state === "EXTRACTION_FAILED")
    body = (
      <Alert
        type="error"
        showIcon
        message="The ISATVON extraction failed"
        description="Regenerate the breakdown, or check the SOW, then try again."
      />
    );
  else if (ids.length === 0) body = <EmptyState message={EMPTY_MESSAGE} />;
  else
    body = (
      <>
        {!isEditable(state) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`Read-only: the Workflow is ${state ?? "not in review"}.`}
          />
        )}
        <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 12 }}>
          <Meta>
            {ids.length} Macro Skill{ids.length === 1 ? "" : "s"} · {subs.length} Sub-Skill
            {subs.length === 1 ? "" : "s"}
            {invalid ? ` · ${invalid} invalid` : ""}
          </Meta>
          <Flex gap={8}>
            <Button size="small" onClick={() => setCollapsed(new Set())}>
              Expand all
            </Button>
            <Button size="small" onClick={() => setCollapsed(new Set(ids))}>
              Collapse all
            </Button>
          </Flex>
        </Flex>
        <IsatvonGrid groups={groups ?? []} rows={rows} onToggle={toggle} />
        <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
          <Meta>{metadataColumns(groups).length} columns from the ISATVON 1.4 metadata</Meta>
          <Meta>Hover a header for its path · a red cell shows the rule it breaks</Meta>
        </Flex>
      </>
    );

  return (
    <div>
      <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
        ISATVON Workbench
      </Typography.Title>
      <Typography.Text style={{ color: MUTED, display: "block", marginBottom: 20 }}>
        The Macro Skills and Sub-Skills SBAM extracted from the approved SOW (ISATVON 1.4)
      </Typography.Text>
      {body}
    </div>
  );
}
