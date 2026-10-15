"use client";

// Project overview (E4.10, TD §9.3): the pipeline timeline, the document counts, Start processing and the
// Workflows SBAM has generated. There is deliberately no way to create a Workflow here: SBAM generates it
// from the approved SOW, so the Workflow list is read-only.
import { useCustom, useCustomMutation, useList } from "@refinedev/core";
import { Alert, Button, Card, Col, Flex, Row, Statistic, Typography } from "antd";
import dayjs from "dayjs";
import Link from "next/link";
import { useRef, useState } from "react";

import {
  ApiErrorState,
  EmptyState,
  LoadingState,
  StatusBadge,
  StepTimeline,
} from "@/components/ui-kit";
import type { PipelineTimeline, Project, RunAccepted, WorkflowSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  EMPTY_OVERVIEW,
  EMPTY_WORKFLOWS,
  PROJECTS_RESOURCE,
  activeDocuments,
  canStart,
  isRunning,
  pipelinePath,
  startErrorMessage,
  startPath,
  timelineView,
  workflowsPath,
} from "../workspace-api";

const REFRESH_MS = 5000; // while a run is in progress

const newKey = () => globalThis.crypto.randomUUID();

export function OverviewScreen({ projectId }: { projectId: string }) {
  const timeline = useCustom<PipelineTimeline, ApiError>({
    url: pipelinePath(projectId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) =>
        q.state.data && isRunning(q.state.data.data.state) ? REFRESH_MS : false,
    },
  });
  const workflows = useCustom<{ items: WorkflowSummary[] }, ApiError>({
    url: workflowsPath(projectId),
    method: "get",
  });
  const projects = useList<Project, ApiError>({
    resource: PROJECTS_RESOURCE,
    pagination: { mode: "off" },
  });
  const start = useCustomMutation<RunAccepted, ApiError>();
  const sending = useRef(false); // one start request at a time, even on a double click
  const [startError, setStartError] = useState<string | null>(null);

  const project = projects.result.data?.find((p) => p.id === projectId);
  const t = timeline.result.data;

  const onStart = () => {
    if (sending.current) return;
    sending.current = true;
    setStartError(null);
    start.mutate(
      {
        url: startPath(projectId),
        method: "post",
        values: {},
        config: { headers: { "idempotency-key": newKey() } },
      },
      {
        onError: (error) => setStartError(startErrorMessage(error)),
        onSettled: () => {
          sending.current = false;
          void timeline.query.refetch(); // the state moved, or the 409 says why it did not
        },
      },
    );
  };

  if (timeline.query.isLoading) return <LoadingState />;
  if (timeline.query.error)
    return (
      <ApiErrorState error={timeline.query.error} onRetry={() => void timeline.query.refetch()} />
    );
  if (!t) return <LoadingState />;

  const { current, failed } = timelineView(t);
  const documents = activeDocuments(t.counts);
  const running = isRunning(t.state);

  return (
    <Flex vertical gap={16}>
      <Card>
        <Flex justify="space-between" align="start" wrap gap={8}>
          <div>
            <Typography.Title level={1} style={{ marginTop: 0, marginBottom: 0, fontSize: 24 }}>
              {project?.name ?? "Overview"}
            </Typography.Title>
            {project?.client_name ? (
              <Typography.Text type="secondary">{project.client_name}</Typography.Text>
            ) : null}
          </div>
          <StatusBadge status={t.state} />
        </Flex>
        <div style={{ margin: "24px 0 8px" }}>
          <StepTimeline current={current} failed={failed} />
        </div>
      </Card>

      {documents === 0 && t.counts.rejected === 0 ? (
        <Card>
          <EmptyState
            message={EMPTY_OVERVIEW}
            action={
              <Link href={`/projects/${projectId}/documents`}>
                <Button type="primary">Upload documents</Button>
              </Link>
            }
          />
        </Card>
      ) : (
        <Card title="Documents">
          <Row gutter={16}>
            <Col xs={12} md={6}>
              <Statistic title="Uploaded" value={t.counts.uploaded} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Parsed" value={t.counts.parsed} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Rejected" value={t.counts.rejected} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Failed" value={t.counts.failed} />
            </Col>
          </Row>
          <Flex gap={12} align="center" wrap style={{ marginTop: 16 }}>
            <Button
              type="primary"
              disabled={!canStart(t) || running}
              loading={start.mutation.isPending}
              onClick={onStart}
            >
              Start processing
            </Button>
            <Link href={`/projects/${projectId}/documents`}>
              <Button>Manage documents</Button>
            </Link>
            {running ? (
              <Typography.Text type="secondary">Processing is running.</Typography.Text>
            ) : null}
          </Flex>
          {startError ? (
            <Alert style={{ marginTop: 12 }} type="warning" showIcon message={startError} />
          ) : null}
        </Card>
      )}

      <Card title="Workflows">
        {workflows.query.isLoading ? (
          <LoadingState />
        ) : workflows.query.error ? (
          <ApiErrorState
            error={workflows.query.error}
            onRetry={() => void workflows.query.refetch()}
          />
        ) : (workflows.result.data?.items ?? []).length === 0 ? (
          <EmptyState message={EMPTY_WORKFLOWS} />
        ) : (
          <Flex vertical gap={8}>
            {workflows.result.data!.items.map((w) => (
              <Flex key={w.id} justify="space-between" align="center">
                <Link href={`/projects/${projectId}/workflows/${w.id}`}>Workflow v{w.version}</Link>
                <Flex gap={12} align="center">
                  <Typography.Text type="secondary">
                    {dayjs(w.created_at).format("D MMM YYYY")}
                  </Typography.Text>
                  <StatusBadge status={w.state} />
                </Flex>
              </Flex>
            ))}
          </Flex>
        )}
      </Card>
    </Flex>
  );
}
