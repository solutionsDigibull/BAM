"use client";

// The project's Workflows, newest first (read-only: SBAM creates them when a SOW is approved). Each row
// shows where the Workflow is in its life and links to it.
import { useCustom } from "@refinedev/core";
import { Card, Flex, Table, Typography } from "antd";
import dayjs from "dayjs";
import Link from "next/link";

import { ApiErrorState, EmptyState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { WorkflowSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { workflowsPath } from "../../workspace-api";
import { nextStep } from "./[workflowId]/workflow-steps";

export function WorkflowsScreen({ projectId }: { projectId: string }) {
  const list = useCustom<{ items: WorkflowSummary[] }, ApiError>({
    url: workflowsPath(projectId),
    method: "get",
  });
  const items = list.result.data?.items ?? [];
  return (
    <Card>
      <Typography.Title level={1} style={{ marginTop: 0, fontSize: 24 }}>
        Workflows
      </Typography.Title>
      {list.query.isLoading ? (
        <LoadingState />
      ) : list.query.error ? (
        <ApiErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState message="SBAM creates the Workflow after the SOW is approved." />
      ) : (
        <Table
          rowKey="id"
          pagination={false}
          dataSource={items}
          columns={[
            {
              title: "Workflow",
              render: (_: unknown, w: WorkflowSummary) => (
                <Link href={`/projects/${projectId}/workflows/${w.id}`}>{w.name}</Link>
              ),
            },
            { title: "Version", dataIndex: "version", width: 100 },
            {
              title: "Status",
              dataIndex: "state",
              width: 200,
              render: (s: string) => <StatusBadge status={s} />,
            },
            {
              title: "Next",
              render: (_: unknown, w: WorkflowSummary) => {
                const step = nextStep(w.state);
                return step ? (
                  <Link href={`/projects/${projectId}/workflows/${w.id}/${step.path}`}>
                    {step.label}
                  </Link>
                ) : (
                  <Typography.Text type="secondary">Nothing to do</Typography.Text>
                );
              },
            },
            {
              title: "Created",
              dataIndex: "created_at",
              width: 130,
              render: (d: string) => (
                <Flex>
                  <Typography.Text type="secondary">
                    {dayjs(d).format("D MMM YYYY")}
                  </Typography.Text>
                </Flex>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}
