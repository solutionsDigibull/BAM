"use client";

// Documents screen (E4.10, E4.11, TD §9.3): upload files, watch them go through scan and parse, retry
// the ones that failed, and delete or restore them (recycle bin, 90 days). One request lists the
// documents with the deleted ones (`include_deleted`); the two tabs split it. It is asked again every
// few seconds while the pipeline is ingesting or any document is still in flight, and then it stops.
import { useCustom } from "@refinedev/core";
import { Card, Flex, Tabs } from "antd";
import dynamic from "next/dynamic";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { DocumentOut, PipelineTimeline } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { EMPTY_DOCUMENTS, documentsResource, pipelinePath } from "../../workspace-api";
import { POLL_MS, countActive, inBin, inList, shouldPoll } from "./document-state";
import { DocumentsTable } from "./documents-table";
import { RecycleBin } from "./recycle-bin";

// Uppy needs the browser: load it on the client only.
const UploadPanel = dynamic(() => import("./upload-panel"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export { countActive };

export function DocumentsScreen({ projectId }: { projectId: string }) {
  const timeline = useCustom<PipelineTimeline, ApiError>({
    url: pipelinePath(projectId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) => (q.state.data?.data.state === "INGESTING" ? POLL_MS : false),
    },
  });
  const docs = useCustom<{ items: DocumentOut[] }, ApiError>({
    url: documentsResource(projectId),
    method: "get",
    config: { query: { include_deleted: "true" } },
    queryOptions: {
      refetchInterval: (q) =>
        shouldPoll(q.state.data?.data.items ?? [], timeline.result.data?.state) ? POLL_MS : false,
    },
  });
  const all = docs.result.data?.items ?? [];
  const listed = inList(all);
  const binned = inBin(all);
  const refresh = () => {
    void docs.query.refetch();
    void timeline.query.refetch();
  };

  return (
    <Flex vertical gap={16}>
      <Card title="Upload documents">
        <UploadPanel
          projectId={projectId}
          active={countActive(all)}
          documents={all}
          onUploaded={refresh}
        />
      </Card>
      <Card>
        {docs.query.isLoading ? (
          <LoadingState />
        ) : docs.query.error ? (
          <ApiErrorState error={docs.query.error} onRetry={() => void docs.query.refetch()} />
        ) : (
          <Tabs
            items={[
              {
                key: "documents",
                label: `Documents (${listed.length})`,
                children:
                  listed.length === 0 ? (
                    <EmptyState message={EMPTY_DOCUMENTS} />
                  ) : (
                    <DocumentsTable projectId={projectId} documents={listed} onChanged={refresh} />
                  ),
              },
              {
                key: "bin",
                label: `Recycle bin (${binned.length})`,
                children: (
                  <RecycleBin projectId={projectId} documents={binned} onChanged={refresh} />
                ),
              },
            ]}
          />
        )}
      </Card>
    </Flex>
  );
}
