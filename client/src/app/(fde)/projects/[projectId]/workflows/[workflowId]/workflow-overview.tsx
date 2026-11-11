"use client";

// One Workflow at a glance: where it is in its life, what to do next, and the way into each stage.
import { useCustom } from "@refinedev/core";
import { Alert, Button, Card, Descriptions, Flex, Steps, Typography } from "antd";
import dayjs from "dayjs";
import Link from "next/link";

import { ApiErrorState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { WorkflowSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { workflowsPath } from "../../../workspace-api";
import { LIFECYCLE, isFailure, nextStep, stepIndex } from "./workflow-steps";

const REFRESH_MS = 5000;
const BUSY = new Set(["ISATVON_EXTRACTING", "BUILDING", "CERTIFYING"]);

export function WorkflowOverview({
  projectId,
  workflowId,
}: {
  projectId: string;
  workflowId: string;
}) {
  const list = useCustom<{ items: WorkflowSummary[] }, ApiError>({
    url: workflowsPath(projectId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) =>
        q.state.data?.data.items.some((w) => w.id === workflowId && BUSY.has(w.state))
          ? REFRESH_MS
          : false,
    },
  });
  if (list.query.isLoading) return <LoadingState />;
  if (list.query.error)
    return <ApiErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />;
  const w = list.result.data?.items.find((x) => x.id === workflowId);
  if (!w)
    return <Alert type="warning" showIcon message="This Workflow was not found in the project." />;

  const base = `/projects/${projectId}/workflows/${w.id}`;
  const at = stepIndex(w.state);
  const next = nextStep(w.state);
  return (
    <Flex vertical gap={16}>
      <Card>
        <Flex justify="space-between" align="start" wrap gap={8}>
          <div>
            <Typography.Title level={1} style={{ marginTop: 0, marginBottom: 0, fontSize: 24 }}>
              {w.name}
            </Typography.Title>
            <Typography.Text type="secondary">
              Version {w.version} · created {dayjs(w.created_at).format("D MMM YYYY, HH:mm")}
            </Typography.Text>
          </div>
          <StatusBadge status={w.state} />
        </Flex>
        <Steps
          style={{ marginTop: 24 }}
          size="small"
          current={Math.max(at, 0)}
          status={isFailure(w.state) ? "error" : w.state === "FROZEN" ? "finish" : "process"}
          items={LIFECYCLE.map((s) => ({
            title: <Link href={`${base}/${s.path}`}>{s.label}</Link>,
          }))}
        />
      </Card>

      {next ? (
        <Alert
          type={isFailure(w.state) ? "error" : "info"}
          showIcon
          message={next.hint}
          action={
            <Link href={`${base}/${next.path}`}>
              <Button type="primary">{next.label}</Button>
            </Link>
          }
        />
      ) : null}

      <Card title="Details">
        <Descriptions column={{ xs: 1, md: 2 }} size="small">
          <Descriptions.Item label="SOW">
            <Link href={`/projects/${projectId}/sow`}>Open the approved SOW</Link>
          </Descriptions.Item>
          <Descriptions.Item label="Created by">{w.created_by}</Descriptions.Item>
          <Descriptions.Item label="Manifests">
            <Link href={`${base}/manifests`}>View</Link>
          </Descriptions.Item>
          <Descriptions.Item label="Code">
            <Link href={`${base}/code`}>View</Link>
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Flex>
  );
}
