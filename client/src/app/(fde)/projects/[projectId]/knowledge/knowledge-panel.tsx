"use client";

// Knowledge status and coverage (E5.6, TD §9 "Knowledge Explorer": coverage panel). The latest knowledge
// version: its status, times and counts, progress while it builds, every document's coverage with the
// failed ones first, Rebuild and the OKF bundle download. The API decides: the buttons are conveniences
// (409 KNOWLEDGE_BUILDING, KNOWLEDGE_NOT_READY). Failure reasons are codes, rendered as plain text.
import { useCustom, useCustomMutation } from "@refinedev/core";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Flex,
  Progress,
  Row,
  Statistic,
  Table,
  Typography,
} from "antd";
import dayjs from "dayjs";
import Link from "next/link";
import { useRef } from "react";

import {
  ApiErrorState,
  EmptyState,
  HashChip,
  LoadingState,
  StatusBadge,
} from "@/components/ui-kit";
import type {
  CoverageRow,
  KnowledgeVersionList,
  KnowledgeVersionSummary,
  RunAccepted,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  ALL_FAILED,
  COVERAGE_UNAVAILABLE,
  EMPTY_KNOWLEDGE,
  POLL_MS,
  canDownload,
  isBuilding,
  knowledgePath,
  latest,
  okfDownloadUrl,
  okfFileName,
  rebuildErrorMessage,
  rebuildPath,
} from "./knowledge-api";

const newKey = () => globalThis.crypto.randomUUID();
const when = (value: string | null | undefined) =>
  value ? dayjs(value).format("D MMM YYYY, HH:mm") : "—";

export function KnowledgePanel({
  projectId,
  projectName,
  onVersion,
}: {
  projectId: string;
  projectName: string;
  /** The latest version, for the explorer below the panel (E5.5). */
  onVersion?: (version: KnowledgeVersionSummary | undefined) => void;
}) {
  const { message } = App.useApp();
  const list = useCustom<KnowledgeVersionList, ApiError>({
    url: knowledgePath(projectId),
    method: "get",
    queryOptions: {
      // Polls only while the newest version builds; React Query pauses it while the tab is hidden.
      refetchInterval: (q) => (isBuilding(latest(q.state.data?.data?.versions)) ? POLL_MS : false),
      refetchIntervalInBackground: false,
    },
  });
  const rebuild = useCustomMutation<RunAccepted, ApiError>();
  const sending = useRef(false); // one rebuild request, even on a double click

  const current = latest(list.result.data?.versions);
  onVersion?.(current);

  const onRebuild = () => {
    if (sending.current) return;
    sending.current = true;
    rebuild.mutate(
      {
        url: rebuildPath(projectId),
        method: "post",
        values: {},
        config: { headers: { "idempotency-key": newKey() } },
      },
      {
        onSuccess: () => void message.success("Rebuild started."),
        onError: (error) => void message.error(rebuildErrorMessage(error)),
        onSettled: () => {
          sending.current = false;
          void list.query.refetch();
        },
      },
    );
  };

  if (list.query.isLoading) return <LoadingState />;
  if (list.query.error)
    return <ApiErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />;
  if (!current)
    return (
      <Card>
        <EmptyState
          message={EMPTY_KNOWLEDGE}
          action={
            <Link href={`/projects/${projectId}`}>
              <Button type="primary">Go to overview</Button>
            </Link>
          }
        />
      </Card>
    );

  const building = isBuilding(current);
  const rows = current.documents ?? [];
  const failed = rows.filter((r) => r.status === "FAILED");
  const allFailed = rows.length > 0 && failed.length === rows.length;
  const counts = current.counts;
  const coverageMissing = !current.coverage || rows.length === 0;

  return (
    <Flex vertical gap={16}>
      <Card>
        <Flex justify="space-between" align="start" wrap gap={8}>
          <div>
            <Typography.Title level={1} style={{ marginTop: 0, marginBottom: 0, fontSize: 24 }}>
              Knowledge v{current.version}
            </Typography.Title>
            <Typography.Text type="secondary">
              Started {when(current.created_at)} · Finished {when(current.finished_at)}
            </Typography.Text>
          </div>
          <StatusBadge status={current.status} />
        </Flex>

        {building ? (
          <div style={{ marginTop: 16 }} aria-label="Building progress">
            <Progress percent={99} status="active" showInfo={false} />
            <Typography.Text type="secondary">
              Building the knowledge base from your documents…
            </Typography.Text>
          </div>
        ) : null}

        {allFailed ? (
          <Alert style={{ marginTop: 16 }} type="error" showIcon message={ALL_FAILED} />
        ) : null}
        {current.status === "FAILED" && current.error_reason ? (
          <Alert
            style={{ marginTop: 16 }}
            type="error"
            showIcon
            message="The knowledge build failed"
            description={current.error_reason}
          />
        ) : null}

        <Row gutter={16} style={{ marginTop: 16 }}>
          <Col xs={12} md={6}>
            <Statistic title="Documents" value={counts?.documents ?? 0} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="Entities" value={counts?.entities ?? 0} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="Relations" value={counts?.relations ?? 0} />
          </Col>
          <Col xs={12} md={6}>
            <Statistic title="Chunks" value={counts?.chunks ?? 0} />
          </Col>
        </Row>

        <Flex gap={12} align="center" wrap style={{ marginTop: 16 }}>
          <Button disabled={building} loading={rebuild.mutation.isPending} onClick={onRebuild}>
            Rebuild
          </Button>
          {canDownload(current) ? (
            <a
              href={okfDownloadUrl(projectId, current.id)}
              download={okfFileName(projectName, current.version)}
            >
              <Button type="primary">Download OKF bundle</Button>
            </a>
          ) : (
            <Button type="primary" disabled>
              Download OKF bundle
            </Button>
          )}
          {current.okf_sha256 ? <HashChip value={current.okf_sha256} /> : null}
        </Flex>
      </Card>

      <Card title="Coverage">
        {coverageMissing ? (
          <Typography.Text type="secondary">{COVERAGE_UNAVAILABLE}</Typography.Text>
        ) : (
          <Table<CoverageRow>
            rowKey="document_version_id"
            size="small"
            pagination={false}
            dataSource={[...failed, ...rows.filter((r) => r.status !== "FAILED")]}
            columns={[
              { title: "Document", dataIndex: "title", render: (t: string) => t || "—" },
              {
                title: "Status",
                dataIndex: "status",
                render: (s: string) => <StatusBadge status={s} />,
              },
              { title: "Chunks", dataIndex: "chunks" },
              { title: "Entities", dataIndex: "entities" },
              {
                title: "Reason",
                dataIndex: "reason",
                render: (reason: string | null | undefined) => reason ?? "",
              },
              {
                title: "",
                key: "action",
                render: (_: unknown, row: CoverageRow) =>
                  row.status === "FAILED" ? (
                    <Button size="small" disabled={building} onClick={onRebuild}>
                      Rebuild
                    </Button>
                  ) : null,
              },
            ]}
          />
        )}
      </Card>
    </Flex>
  );
}
