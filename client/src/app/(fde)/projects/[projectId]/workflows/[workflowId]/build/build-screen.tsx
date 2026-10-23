"use client";

// Build & Test (TD §9.3, E12.6): every Sub-Skill × gate of the latest build, live while anything builds,
// with the findings of a failed gate and Retry for a Sub-Skill waiting in NEEDS_FDE_REVIEW.
// GET W/builds and GET W/sub-skills/{id}/gates come with #117 / #119; until then the screen is built
// against sbam_contracts.build_dashboard (#312).

import { useCustom, useCustomMutation, type BaseRecord } from "@refinedev/core";
import {
  Alert,
  App,
  Drawer,
  Empty,
  Flex,
  Input,
  Modal,
  Segmented,
  Spin,
  Tag,
  Typography,
} from "antd";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState, Meta, MUTED } from "@/components/ui-kit";
import type { BuildRow, BuildsResponse, GateName, GatesResponse } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import type { GridActions } from "./build-grid";
import {
  buildRows,
  buildsUrl,
  codeHref,
  GATE_LABEL,
  gatesUrl,
  pollMs,
  retryUrl,
  summary,
} from "./build-api";

const BuildGrid = dynamic(() => import("./build-grid"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const EMPTY_MESSAGE = "Nothing is building yet.";
const newKey = () => globalThis.crypto.randomUUID();

function FindingsDrawer({
  projectId,
  workflowId,
  row,
  gate,
  onClose,
}: {
  projectId: string;
  workflowId: string;
  row: BuildRow;
  gate: GateName;
  onClose: () => void;
}) {
  const [attempt, setAttempt] = useState(row.attempt ?? 0);
  const { result, query } = useCustom<GatesResponse, ApiError>({
    url: gatesUrl(projectId, workflowId, row.sub_skill_id),
    method: "get",
    config: { query: { attempt } },
  });
  const gates = Array.isArray(result.data?.gates) ? result.data.gates : [];
  // the gate that was clicked first, then the other failures of the same attempt
  const shown = [
    ...gates.filter((g) => g.gate === gate),
    ...gates.filter((g) => g.gate !== gate && g.status === "FAILED"),
  ];
  return (
    <Drawer
      open
      width={640}
      onClose={onClose}
      title={`${row.skill_id} · ${GATE_LABEL[gate]} findings`}
    >
      <Flex vertical gap={16}>
        <Segmented
          aria-label="Attempt"
          value={attempt}
          onChange={(v) => setAttempt(Number(v))}
          options={Array.from({ length: (row.attempt ?? 0) + 1 }, (_, n) => ({
            value: n,
            label: `Attempt ${n + 1}`,
          }))}
        />
        {query.isLoading ? (
          <Spin />
        ) : query.isError ? (
          <ApiErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : shown.length === 0 ? (
          <Empty description="No findings for this attempt" />
        ) : (
          shown.map((g) => (
            <section key={g.gate} aria-label={`${GATE_LABEL[g.gate]} gate`}>
              <Flex align="center" gap={8} style={{ marginBottom: 8 }}>
                <Typography.Text strong>{GATE_LABEL[g.gate]}</Typography.Text>
                <Tag color={g.status === "FAILED" ? "error" : "default"}>{g.status}</Tag>
                {g.tool_version && <Meta>{g.tool_version}</Meta>}
              </Flex>
              {(g.findings ?? []).length === 0 && <Meta>No findings recorded.</Meta>}
              {(g.findings ?? []).map((f, i) => (
                <div
                  key={i}
                  role="listitem"
                  style={{ borderLeft: "3px solid #fecaca", padding: "6px 12px", marginBottom: 8 }}
                >
                  <Flex gap={8} wrap align="center">
                    <Tag>{f.code}</Tag>
                    {f.file && (
                      <Link
                        href={codeHref(projectId, workflowId, {
                          subId: row.sub_skill_id,
                          attempt,
                          file: f.file,
                          line: f.line,
                        })}
                      >
                        {f.file}
                        {f.line ? `:${f.line}` : ""}
                      </Link>
                    )}
                  </Flex>
                  {/* text only: a message holding markup is shown as those characters (E12.6-T05) */}
                  <Typography.Paragraph style={{ margin: "6px 0 0", whiteSpace: "pre-wrap" }}>
                    {f.message}
                  </Typography.Paragraph>
                </div>
              ))}
            </section>
          ))
        )}
      </Flex>
    </Drawer>
  );
}

export function BuildScreen({ projectId, workflowId }: { projectId: string; workflowId: string }) {
  const { message } = App.useApp();
  const builds = useCustom<BuildsResponse, ApiError>({
    url: buildsUrl(projectId, workflowId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) => pollMs(q.state.data?.data, q.state.fetchFailureCount),
      retry: false, // the poll interval is the retry, with backoff
    },
  });
  // `useCustom` hands back `{}` while loading or after an error: only a body with rows counts as data.
  const raw = builds.result.data as Partial<BuildsResponse> | undefined;
  const data = Array.isArray(raw?.sub_skills) ? (raw as BuildsResponse) : undefined;
  const rows = useMemo(() => buildRows(data), [data]);
  const counts = summary(rows);
  const [findings, setFindings] = useState<{ row: BuildRow; gate: GateName } | null>(null);
  const [retry, setRetry] = useState<BuildRow | null>(null);
  const [reason, setReason] = useState("");
  const { mutateAsync, mutation } = useCustomMutation<BaseRecord, ApiError>();

  const actions: GridActions = {
    onFindings: (row, gate) => setFindings({ row, gate }),
    onRetry: (row) => setRetry(row),
  };

  const sendRetry = async () => {
    if (!retry || !reason.trim()) return;
    try {
      await mutateAsync({
        url: retryUrl(projectId, workflowId, retry.sub_skill_id),
        method: "post",
        values: { reason: reason.trim() },
        config: { headers: { "idempotency-key": newKey() } },
        successNotification: false,
        errorNotification: false,
      });
      message.success(`Retry started for ${retry.skill_id}`);
      setRetry(null);
      setReason("");
      await builds.query.refetch();
    } catch (e) {
      message.error((e as ApiError).message);
    }
  };

  let body;
  if (builds.query.isLoading) body = <LoadingState />;
  else if (builds.query.isError && !data)
    body = <ApiErrorState error={builds.query.error} onRetry={() => builds.query.refetch()} />;
  else if (rows.length === 0) body = <EmptyState message={EMPTY_MESSAGE} />;
  else
    body = (
      <>
        {builds.query.isError && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="Connection lost: showing the last update, retrying with backoff."
          />
        )}
        <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 12 }}>
          <Meta>
            {counts.total} Sub-Skill{counts.total === 1 ? "" : "s"}
            {counts.failed ? ` · ${counts.failed} with a failed gate` : ""}
            {counts.review ? ` · ${counts.review} waiting for review` : ""}
          </Meta>
          <Meta>{data?.active ? "Live: updates every 3 s" : "Not building"}</Meta>
        </Flex>
        <BuildGrid rows={rows} actions={actions} />
        <Meta>
          Click a failed gate for its findings · rows waiting for your review are highlighted
        </Meta>
      </>
    );

  return (
    <div>
      <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
        Build &amp; Test
      </Typography.Title>
      <Typography.Text style={{ color: MUTED, display: "block", marginBottom: 20 }}>
        Every Sub-Skill through the contract, AST and static gates and the loop tests
      </Typography.Text>
      {body}
      {findings && (
        <FindingsDrawer
          projectId={projectId}
          workflowId={workflowId}
          row={findings.row}
          gate={findings.gate}
          onClose={() => setFindings(null)}
        />
      )}
      <Modal
        open={!!retry}
        title={retry ? `Retry ${retry.skill_id}` : ""}
        okText="Retry"
        okButtonProps={{ disabled: !reason.trim() }}
        confirmLoading={mutation.isPending}
        onOk={sendRetry}
        onCancel={() => {
          setRetry(null);
          setReason("");
        }}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">
          The build runs again from its manifest. Say what you fixed; it is kept with the build.
        </Typography.Paragraph>
        <Input.TextArea
          aria-label="Reason"
          placeholder="e.g. raised max_iterations bound in V, fixed the handoff table name"
          rows={3}
          maxLength={500}
          showCount
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Modal>
    </div>
  );
}
