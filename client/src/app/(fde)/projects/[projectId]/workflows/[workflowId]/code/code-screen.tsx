"use client";

// The code viewer (TD §9.3, E11.5): a Sub-Skill's generated files per attempt, read only. Lines
// outside the LLM_BODY slots are protected template code and are shaded; gate findings mark their
// line. Code is rendered as text, never as HTML. The code pane is a read-only Monaco editor, loaded
// in the browser only (it cannot render on the server); a skeleton shows while it loads.
import { useCustom } from "@refinedev/core";
import { Alert, Segmented, Tag, Typography } from "antd";
import dynamic from "next/dynamic";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { CodeAttemptList, CodeDiff, CodeFileView } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  attemptsUrl,
  diffKind,
  diffUrl,
  fileUrl,
  findingText,
  toLines,
  type DiffLineKind,
} from "./code-api";
import { MONO } from "./code-lines";

const MonacoCode = dynamic(() => import("./monaco-code"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const EMPTY_MESSAGE = "Code not generated yet";
const DIFF_BG: Record<DiffLineKind, string> = {
  add: "rgba(46, 160, 67, 0.22)",
  del: "rgba(248, 81, 73, 0.22)",
  hunk: "rgba(56, 139, 253, 0.18)",
  meta: "transparent",
  context: "transparent",
};

export function CodeScreen({
  projectId,
  workflowId,
  subId,
}: {
  projectId: string;
  workflowId: string;
  subId: string;
}) {
  const list = useCustom<CodeAttemptList, ApiError>({
    url: attemptsUrl(projectId, workflowId, subId),
    method: "get",
  });
  const [attempt, setAttempt] = useState<number | null>(null);
  const [path, setPath] = useState<string | null>(null);
  const [compare, setCompare] = useState(false);

  const raw = list.result.data as unknown;
  const items = Array.isArray((raw as CodeAttemptList | null)?.items)
    ? (raw as CodeAttemptList).items
    : [];
  const current = items.find((i) => i.attempt === attempt) ?? items[items.length - 1];
  const files = current?.files ?? [];
  const shownPath = files.find((f) => f.path === path)?.path ?? files[0]?.path ?? null;
  const previous = current ? items.filter((i) => i.attempt < current.attempt).at(-1) : undefined;

  if (list.query.isLoading) return <LoadingState />;
  if (list.query.error)
    return <ApiErrorState error={list.query.error} onRetry={() => list.query.refetch()} />;
  if (!current) return <EmptyState message={EMPTY_MESSAGE} />;

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <Segmented
          aria-label="Attempt"
          value={current.attempt}
          onChange={(v) => {
            setAttempt(Number(v));
            setCompare(false);
          }}
          options={items.map((i) => ({
            value: i.attempt,
            label: i.attempt === 0 ? "First pass" : `Correction ${i.attempt}`,
          }))}
        />
        <Tag>{current.status}</Tag>
        {previous && files.length > 0 && (
          <Segmented
            aria-label="View"
            value={compare ? "diff" : "code"}
            onChange={(v) => setCompare(v === "diff")}
            options={[
              { value: "code", label: "Code" },
              { value: "diff", label: `Changes since attempt ${previous.attempt}` },
            ]}
          />
        )}
      </div>
      {current.correction_reason && (
        <Alert
          type="info"
          showIcon
          message="Correction reason"
          description={current.correction_reason}
        />
      )}
      {files.length === 0 ? (
        <>
          <EmptyState message="This attempt produced no files" />
          <Findings findings={current.findings} />
        </>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 16 }}>
          <nav aria-label="Files" style={{ display: "grid", gap: 4, alignContent: "start" }}>
            {files.map((f) => (
              <button
                key={f.path}
                type="button"
                aria-current={f.path === shownPath}
                onClick={() => setPath(f.path)}
                style={{
                  ...MONO,
                  textAlign: "left",
                  padding: "4px 8px",
                  cursor: "pointer",
                  fontWeight: f.path === shownPath ? 600 : 400,
                }}
              >
                {f.path}
              </button>
            ))}
          </nav>
          {shownPath &&
            (compare && previous ? (
              <DiffPane
                projectId={projectId}
                workflowId={workflowId}
                subId={subId}
                from={previous.attempt}
                to={current.attempt}
                path={shownPath}
              />
            ) : (
              <FilePane
                projectId={projectId}
                workflowId={workflowId}
                subId={subId}
                attempt={current.attempt}
                path={shownPath}
              />
            ))}
        </div>
      )}
    </div>
  );
}

function Findings({ findings }: { findings: CodeFileView["findings"] }) {
  if (findings.length === 0) return null;
  return (
    <ul aria-label="Findings" style={{ margin: 0, paddingLeft: 20 }}>
      {findings.map((f, i) => (
        <li key={i}>
          {f.line ? `Line ${f.line}: ` : ""}
          {findingText(f)}
        </li>
      ))}
    </ul>
  );
}

function FilePane(props: {
  projectId: string;
  workflowId: string;
  subId: string;
  attempt: number;
  path: string;
}) {
  const { result, query } = useCustom<CodeFileView, ApiError>({
    url: fileUrl(props.projectId, props.workflowId, props.subId, props.attempt),
    method: "get",
    config: { query: { path: props.path } },
  });
  // `useCustom` hands back `{}` while loading: only a body with text content is a file.
  const data = result.data as Partial<CodeFileView> | undefined;
  const view = typeof data?.content === "string" ? (data as CodeFileView) : undefined;
  const parsed = useMemo(
    () => (view ? toLines(view.content, view.slots ?? [], view.findings ?? []) : null),
    [view],
  );
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!view || !parsed) return <EmptyState message={EMPTY_MESSAGE} />;
  return (
    <div>
      {view.truncated && (
        <Alert
          type="warning"
          showIcon
          message="This file is longer than the viewer shows; it was cut."
        />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Shaded lines are protected template code; only the unshaded lines were written by the model.
      </Typography.Text>
      <MonacoCode path={view.path} lines={parsed.lines} />
      <Findings findings={parsed.loose} />
    </div>
  );
}

function DiffPane(props: {
  projectId: string;
  workflowId: string;
  subId: string;
  from: number;
  to: number;
  path: string;
}) {
  const { result, query } = useCustom<CodeDiff, ApiError>({
    url: diffUrl(props.projectId, props.workflowId, props.subId, props.from, props.to),
    method: "get",
    config: { query: { path: props.path } },
  });
  const data = result.data as Partial<CodeDiff> | undefined;
  const diff = typeof data?.unified === "string" ? (data as CodeDiff) : undefined;
  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!diff) return <EmptyState message={EMPTY_MESSAGE} />;
  if (!diff.changed) return <EmptyState message="No changes in this file" />;
  return (
    <div
      role="region"
      aria-label={`Changes to ${diff.path}`}
      style={{ ...MONO, overflowX: "auto" }}
    >
      {diff.unified.split("\n").map((line, i) => (
        <pre
          key={i}
          data-kind={diffKind(line)}
          style={{ margin: 0, background: DIFF_BG[diffKind(line)] }}
        >
          {line}
        </pre>
      ))}
      {diff.truncated && <Alert type="warning" showIcon message="The diff was cut." />}
    </div>
  );
}
