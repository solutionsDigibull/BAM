"use client";

// Documents screen (E4.10, TD §9.3): upload files and see what the project holds. The list here is the
// minimum (name, size, state); the stage chips, retry and recycle bin are E4.11 (#77) and build on it.
import { useList } from "@refinedev/core";
import { Card, Flex, Table, Typography } from "antd";
import dynamic from "next/dynamic";

import { ApiErrorState, EmptyState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { DocumentOut } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { EMPTY_DOCUMENTS, documentsResource } from "../../workspace-api";

// Uppy needs the browser: load it on the client only.
const UploadPanel = dynamic(() => import("./upload-panel"), {
  ssr: false,
  loading: () => <LoadingState />,
});

const INACTIVE = ["REJECTED", "DELETED", "PURGED"];

/** Documents that count toward the project's 20-file limit. */
export const countActive = (docs: DocumentOut[]): number =>
  docs.filter((d) => !d.deleted_at && !INACTIVE.includes(d.state)).length;

const size = (bytes: number): string =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export function DocumentsScreen({ projectId }: { projectId: string }) {
  const docs = useList<DocumentOut, ApiError>({
    resource: documentsResource(projectId),
    pagination: { mode: "off" },
  });
  const items = (docs.result.data ?? []).filter((d) => !d.deleted_at);

  return (
    <Flex vertical gap={16}>
      <Card title="Upload documents">
        <UploadPanel
          projectId={projectId}
          active={countActive(docs.result.data ?? [])}
          onUploaded={() => void docs.query.refetch()}
        />
      </Card>
      <Card title="Documents">
        {docs.query.isLoading ? (
          <LoadingState />
        ) : docs.query.error ? (
          <ApiErrorState error={docs.query.error} onRetry={() => void docs.query.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState message={EMPTY_DOCUMENTS} />
        ) : (
          <Table<DocumentOut>
            rowKey="id"
            dataSource={items}
            pagination={false}
            columns={[
              {
                title: "Name",
                dataIndex: "title",
                render: (t: string) => <Typography.Text>{t}</Typography.Text>,
              },
              { title: "Size", dataIndex: "size", render: size },
              {
                title: "State",
                dataIndex: "state",
                render: (s: DocumentOut["state"]) => <StatusBadge status={s} />,
              },
            ]}
          />
        )}
      </Card>
    </Flex>
  );
}
