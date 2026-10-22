"use client";

// The recycle bin of the Documents screen (E4.11): this project's deleted documents, when each is purged
// and a Restore button. A document can be restored for 90 days; after the purge the API answers 410.
import { useCustomMutation } from "@refinedev/core";
import { App, Button, Table, Typography } from "antd";
import dayjs from "dayjs";
import { useState } from "react";

import { EmptyState } from "@/components/ui-kit";
import type { DocumentOut } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { restorePath } from "../../workspace-api";
import { daysLeftText, formatSize, restoreErrorMessage } from "./document-state";

export const EMPTY_BIN = "The recycle bin is empty.";

type Props = { projectId: string; documents: DocumentOut[]; onChanged: () => void };

export function RecycleBin({ projectId, documents, onChanged }: Props) {
  const { message } = App.useApp();
  const restore = useCustomMutation<DocumentOut, ApiError>();
  const [busy, setBusy] = useState<string | null>(null);

  const onRestore = (doc: DocumentOut) => {
    if (busy) return;
    setBusy(doc.id);
    restore.mutate(
      { url: restorePath(projectId, doc.id), method: "post", values: {} },
      {
        onSuccess: () => void message.success(`${doc.title} restored`),
        onError: (error) => {
          void message.error(restoreErrorMessage(error));
        },
        onSettled: () => {
          setBusy(null);
          onChanged(); // restored, or gone for good: either way the bin changed
        },
      },
    );
  };

  if (documents.length === 0) return <EmptyState message={EMPTY_BIN} />;

  return (
    <Table<DocumentOut>
      rowKey="id"
      dataSource={documents}
      pagination={false}
      columns={[
        {
          title: "Name",
          dataIndex: "title",
          render: (title: string) => <Typography.Text>{title}</Typography.Text>,
        },
        { title: "Size", dataIndex: "size", render: formatSize },
        {
          title: "Deleted",
          dataIndex: "deleted_at",
          render: (at: string | null) => (at ? dayjs(at).format("D MMM YYYY") : ""),
        },
        {
          title: "Purged on",
          dataIndex: "purge_after",
          render: (at: string | null) =>
            at ? `${dayjs(at).format("D MMM YYYY")} (${daysLeftText(at)})` : "",
        },
        {
          title: "Actions",
          key: "actions",
          render: (_: unknown, doc) => (
            <Button
              size="small"
              disabled={busy !== null}
              onClick={() => onRestore(doc)}
              aria-label={`Restore ${doc.title}`}
            >
              Restore
            </Button>
          ),
        },
      ]}
    />
  );
}
