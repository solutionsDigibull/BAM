"use client";

// The whole-Workflow code preview (ADR 0028, WG-15, #763): every file the build stored, grouped by
// Sub-Skill plus the Workflow's own, each with a tag (reused, customized, generated), the gates of
// the newest attempt and the findings on their lines. Read only: nothing here can change a file.
// Code is rendered as text, never as HTML; the code pane is the same read-only Monaco as the attempt
// viewer, loaded in the browser only.
import { useCustom } from "@refinedev/core";
import { Alert, Tag, Typography } from "antd";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { WorkflowCodeGroup, WorkflowCodeTree, WorkflowFileView } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { findingText, toLines } from "./code-api";
import { MONO } from "./code-lines";
import {
  TAGS,
  TAG_COLOR,
  TAG_HINT,
  TAG_LABEL,
  attemptsLink,
  filesOf,
  problemGates,
  splitPath,
  tagCounts,
  wholeFile,
  workflowCodeUrl,
  workflowFileUrl,
} from "./workflow-code-api";

const MonacoCode = dynamic(() => import("./monaco-code"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const EMPTY_WORKFLOW_MESSAGE = "No code stored for this Workflow yet";

type Picked = { group: string; path: string };

export function WorkflowCodeScreen({
  projectId,
  workflowId,
}: {
  projectId: string;
  workflowId: string;
}) {
  const tree = useCustom<WorkflowCodeTree, ApiError>({
    url: workflowCodeUrl(projectId, workflowId),
    method: "get",
  });
  const [picked, setPicked] = useState<Picked | null>(null);

  // `useCustom` hands back `{}` while loading: only a body with groups is a tree.
  const raw = tree.result.data as Partial<WorkflowCodeTree> | undefined;
  const groups = useMemo<WorkflowCodeGroup[]>(
    () => (Array.isArray(raw?.groups) ? (raw.groups as WorkflowCodeGroup[]) : []),
    [raw],
  );

  if (tree.query.isLoading) return <LoadingState />;
  if (tree.query.error)
    return <ApiErrorState error={tree.query.error} onRetry={() => tree.query.refetch()} />;
  if (filesOf(groups).length === 0) return <EmptyState message={EMPTY_WORKFLOW_MESSAGE} />;

  const first = groups.find((g) => g.files.length > 0);
  const firstPath = first?.files[0]?.path;
  const stillThere =
    picked !== null &&
    groups.some((g) => g.group_id === picked.group && g.files.some((f) => f.path === picked.path));
  const current: Picked | null = stillThere
    ? picked
    : first && firstPath
      ? { group: first.group_id, path: firstPath }
      : null;
  const counts = tagCounts(groups);

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div
        aria-label="Legend"
        style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}
      >
        {TAGS.map((t) => (
          <Tag key={t} color={TAG_COLOR[t]} title={TAG_HINT[t]}>
            {TAG_LABEL[t]}: {counts[t]}
          </Tag>
        ))}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Read only: the code is changed by SBAM, never from this screen.
        </Typography.Text>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 16 }}>
        <nav
          aria-label="Workflow files"
          style={{ display: "grid", gap: 12, alignContent: "start" }}
        >
          {groups.map((g) => (
            <GroupList
              key={g.group_id}
              projectId={projectId}
              workflowId={workflowId}
              group={g}
              current={current}
              onPick={setPicked}
            />
          ))}
        </nav>
        {current && <FilePane projectId={projectId} workflowId={workflowId} picked={current} />}
      </div>
    </div>
  );
}

function GroupList({
  projectId,
  workflowId,
  group,
  current,
  onPick,
}: {
  projectId: string;
  workflowId: string;
  group: WorkflowCodeGroup;
  current: Picked | null;
  onPick: (p: Picked) => void;
}) {
  const failed = problemGates(group);
  const link = attemptsLink(projectId, workflowId, group);
  return (
    <section aria-label={group.label} style={{ display: "grid", gap: 4 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <Typography.Text strong>{group.label}</Typography.Text>
        {group.status && <Tag>{group.status}</Tag>}
        {failed.map((gate) => (
          <Tag key={gate} color="red">
            {gate}
          </Tag>
        ))}
        {link && group.files.length > 0 && <Link href={link}>Attempts and changes</Link>}
      </div>
      {group.files.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Nothing stored yet
        </Typography.Text>
      )}
      {group.files.map((f) => {
        const { dir, name } = splitPath(f.path);
        const active = current?.group === group.group_id && current.path === f.path;
        return (
          <button
            key={f.path}
            type="button"
            aria-current={active}
            title={f.path}
            onClick={() => onPick({ group: group.group_id, path: f.path })}
            style={{
              ...MONO,
              textAlign: "left",
              padding: "4px 8px",
              cursor: "pointer",
              fontWeight: active ? 600 : 400,
              display: "flex",
              gap: 6,
              alignItems: "center",
            }}
          >
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
              {dir && <span style={{ opacity: 0.6 }}>{dir}/</span>}
              {name}
            </span>
            {f.findings.length > 0 && (
              <span
                role="img"
                aria-label={`${f.findings.length} findings`}
                style={{ color: "#d4380d" }}
              >
                ●
              </span>
            )}
            <Tag color={TAG_COLOR[f.tag]} style={{ marginInlineEnd: 0 }}>
              {TAG_LABEL[f.tag]}
            </Tag>
          </button>
        );
      })}
      {group.findings.length > 0 && (
        <ul aria-label={`${group.label} findings`} style={{ margin: 0, paddingLeft: 20 }}>
          {group.findings.map((f, i) => (
            <li key={i}>{findingText(f)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FilePane({
  projectId,
  workflowId,
  picked,
}: {
  projectId: string;
  workflowId: string;
  picked: Picked;
}) {
  const { result, query } = useCustom<WorkflowFileView, ApiError>({
    url: workflowFileUrl(projectId, workflowId),
    method: "get",
    config: { query: { group: picked.group, path: picked.path } },
  });
  const data = result.data as Partial<WorkflowFileView> | undefined;
  const view = typeof data?.content === "string" ? (data as WorkflowFileView) : undefined;
  const parsed = useMemo(
    () => (view ? toLines(view.content, wholeFile(view.content), view.findings ?? []) : null),
    [view],
  );
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!view || !parsed) return <EmptyState message={EMPTY_WORKFLOW_MESSAGE} />;
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <Typography.Text strong style={MONO}>
          {view.path}
        </Typography.Text>
        <Tag color={TAG_COLOR[view.tag]} title={TAG_HINT[view.tag]}>
          {TAG_LABEL[view.tag]}
        </Tag>
      </div>
      {view.truncated && (
        <Alert
          type="warning"
          showIcon
          message="This file is longer than the viewer shows; it was cut."
        />
      )}
      <MonacoCode path={view.path} lines={parsed.lines} />
      {parsed.loose.length > 0 && (
        <ul aria-label="Findings" style={{ margin: 0, paddingLeft: 20 }}>
          {parsed.loose.map((f, i) => (
            <li key={i}>
              {f.line ? `Line ${f.line}: ` : ""}
              {findingText(f)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
