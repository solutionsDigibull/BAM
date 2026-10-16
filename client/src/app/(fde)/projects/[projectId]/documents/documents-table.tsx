"use client";

// The status table of the Documents screen (E4.11): per-file scan / parse / OCR chips with the reason a
// file failed, Retry for failed documents and Delete (to the recycle bin) behind a confirm dialog.
import { SyncOutlined } from "@ant-design/icons";
import { useCustomMutation, useDelete } from "@refinedev/core";
import { App, Button, Flex, Table, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";

import type { DocumentOut, RunAccepted } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { documentsResource, retryPath } from "../../workspace-api";
import {
  canRetry,
  deleteErrorMessage,
  formatSize,
  hasActions,
  retryErrorMessage,
  stageChips,
  stateReason,
  type Chip,
  type ChipStatus,
} from "./document-state";

const COLOR: Record<ChipStatus, string> = {
  pending: "default",
  running: "processing",
  ok: "success",
  failed: "error",
  skipped: "default",
  na: "default",
};
const STAGE_LABEL = { scan: "Scan", parse: "Parse", ocr: "OCR" } as const;

function StageChip({ chip }: { chip: Chip }) {
  return (
    <Tag
      color={COLOR[chip.status]}
      icon={chip.status === "running" ? <SyncOutlined spin /> : undefined}
      style={{ marginInlineEnd: 0 }}
    >
      {STAGE_LABEL[chip.stage]}: {chip.text}
    </Tag>
  );
}

const newKey = () => globalThis.crypto.randomUUID();

type Props = { projectId: string; documents: DocumentOut[]; onChanged: () => void };

export function DocumentsTable({ projectId, documents, onChanged }: Props) {
  const { message, modal } = App.useApp();
  const retry = useCustomMutation<RunAccepted, ApiError>();
  const remove = useDelete<DocumentOut, ApiError>();
  const [busy, setBusy] = useState<string | null>(null); // one action at a time per row

  const onRetry = (doc: DocumentOut) => {
    if (busy) return;
    setBusy(doc.id);
    retry.mutate(
      {
        url: retryPath(projectId, doc.id),
        method: "post",
        values: {},
        config: { headers: { "idempotency-key": newKey() } },
      },
      {
        onSuccess: () => void message.success(`Retrying ${doc.title}`),
        onError: (error) => {
          void message.error(retryErrorMessage(error));
        },
        onSettled: () => {
          setBusy(null);
          onChanged(); // the chips reset to running, or the 409/404 says the list was stale
        },
      },
    );
  };

  const confirmDelete = (doc: DocumentOut) =>
    modal.confirm({
      title: `Delete ${doc.title}?`,
      content: "It moves to the recycle bin and can be restored for 90 days.",
      okText: "Delete",
      okButtonProps: { danger: true },
      onOk: () =>
        new Promise<void>((resolve) => {
          setBusy(doc.id);
          remove.mutate(
            { resource: documentsResource(projectId), id: doc.id, successNotification: false },
            {
              onSuccess: () => void message.success(`${doc.title} moved to the recycle bin`),
              onError: (error) => {
                void message.error(deleteErrorMessage(error));
              },
              onSettled: () => {
                setBusy(null);
                onChanged();
                resolve();
              },
            },
          );
        }),
    });

  return (
    <Table<DocumentOut>
      rowKey="id"
      dataSource={documents}
      pagination={false}
      columns={[
        {
          title: "Name",
          dataIndex: "title",
          render: (title: string, doc) => {
            const reason = stateReason(doc.state);
            return (
              <Flex vertical>
                <Typography.Text>{title}</Typography.Text>
                {reason ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {reason}
                  </Typography.Text>
                ) : null}
              </Flex>
            );
          },
        },
        { title: "Size", dataIndex: "size", render: formatSize },
        {
          title: "Progress",
          dataIndex: "state",
          render: (_: unknown, doc) => (
            <Flex gap={4} wrap>
              {stageChips(doc.state).map((chip) => (
                <Tooltip
                  key={chip.stage}
                  title={
                    chip.stage === "ocr" ? "OCR runs for scanned pages and images." : undefined
                  }
                >
                  <span>
                    <StageChip chip={chip} />
                  </span>
                </Tooltip>
              ))}
            </Flex>
          ),
        },
        {
          title: "Actions",
          key: "actions",
          render: (_: unknown, doc) =>
            !hasActions(doc.state) ? null : (
              <Flex gap={8}>
                {canRetry(doc.state) ? (
                  <Button size="small" disabled={busy !== null} onClick={() => onRetry(doc)}>
                    Retry
                  </Button>
                ) : null}
                <Button
                  size="small"
                  danger
                  disabled={busy !== null}
                  onClick={() => confirmDelete(doc)}
                  aria-label={`Delete ${doc.title}`}
                >
                  Delete
                </Button>
              </Flex>
            ),
        },
      ]}
    />
  );
}
