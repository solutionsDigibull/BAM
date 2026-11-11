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
  Tag,
  Typography,
} from "antd";
import dayjs from "dayjs";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { PreflightBanner } from "@/components/preflight-banner";
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
  retryDocumentPath,
} from "./knowledge-api";
import { KnowledgeDiffCard } from "./knowledge-diff";

const newKey = () => globalThis.crypto.randomUUID();
const when = (value: string | null | undefined) =>
  value ? dayjs(value).format("D MMM YYYY, HH:mm") : "—";

/** A coverage reason for people: `DUPLICATE_OF <id>` names the other copy by its file name. */
function readable(reason: string | null | undefined, rows: CoverageRow[]): string {
  const twin = reason?.match(/^DUPLICATE_OF (\S+)$/)?.[1];
  if (!twin) return reason ?? "";
  const title = rows.find((r) => r.document_version_id === twin)?.title;
  return title ? `Identical file: ${title}` : "Identical to another uploaded file";
}

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
  useEffect(() => {
    onVersion?.(current);
  }, [current?.id, current?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const retry = useCustomMutation<RunAccepted, ApiError>();
  const [retrying, setRetrying] = useState<string | null>(null);
  const onRetry = (documentId: string) => {
    if (retrying) return; // one retry request at a time
    setRetrying(documentId);
    retry.mutate(
      {
        url: retryDocumentPath(projectId, documentId),
        method: "post",
        values: {},
        config: { headers: { "idempotency-key": newKey() } },
      },
      {
        onSuccess: () => void message.success("Document retry started."),
        onError: (error) =>
          void message.error(
            (error as ApiError | null)?.problem?.detail ?? "Could not retry the document.",
          ),
        onSettled: () => setRetrying(null),
      },
    );
  };

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
  const mock = current.coverage?.extraction?.startsWith("mock|") ?? false;
  const anchors = current.coverage?.anchors;
  const anchorTotal = anchors?.total ?? 0;
  const anchorFound = anchors?.found ?? 0;
  const anchorShare = anchorTotal > 0 ? anchorFound / anchorTotal : 1;

  return (
    <Flex vertical gap={16}>
      <PreflightBanner />
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
          <Flex gap={8} align="center">
            {mock ? <Tag color="orange">Mock LLM: synthetic graph</Tag> : null}
            <StatusBadge status={current.status} />
          </Flex>
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

        {anchors && anchorTotal > 0 ? (
          <Alert
            style={{ marginTop: 16 }}
            type={anchorShare < 0.5 ? "warning" : anchorShare < 0.8 ? "info" : "success"}
            showIcon
            message={`${anchorFound} of ${anchorTotal} identifiers from your documents are in the graph`}
            description={
              anchors.missing && anchors.missing.length > 0
                ? `Not found, most frequent first: ${anchors.missing.join(", ")}`
                : undefined
            }
          />
        ) : null}

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

      {current.status === "READY" || current.status === "OUTDATED" ? (
        <KnowledgeDiffCard projectId={projectId} kvId={current.id} version={current.version} />
      ) : null}

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
                render: (reason: string | null | undefined) => readable(reason, rows),
              },
              {
                title: "",
                key: "action",
                render: (_: unknown, row: CoverageRow) =>
                  row.status === "FAILED" ? (
                    <Flex gap={8}>
                      {row.document_id ? (
                        <Button
                          size="small"
                          loading={retrying === row.document_id}
                          onClick={() => onRetry(row.document_id!)}
                        >
                          Retry document
                        </Button>
                      ) : null}
                      <Button size="small" disabled={building} onClick={onRebuild}>
                        Rebuild
                      </Button>
                    </Flex>
                  ) : null,
              },
            ]}
          />
        )}
      </Card>
    </Flex>
  );
}
