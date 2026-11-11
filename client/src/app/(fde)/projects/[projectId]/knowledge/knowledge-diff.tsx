"use client";

// What changed since the version before (or one the user picks): entities and relations gained and lost, by
// name (node ids change on every rebuild). Hidden for the first version and while nothing can be compared.
import { useCustom } from "@refinedev/core";
import { Card, Collapse, Flex, Statistic, Tag, Typography } from "antd";

import type { DiffEntity, KnowledgeDiff } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const diffPath = (projectId: string, kvId: string) =>
  `projects/${projectId}/knowledge/${kvId}/diff`;

const MAX_SHOWN = 40;

function Names({ items, total }: { items: DiffEntity[]; total: number }) {
  if (items.length === 0) return <Typography.Text type="secondary">None</Typography.Text>;
  return (
    <Flex gap={4} wrap>
      {items.slice(0, MAX_SHOWN).map((e) => (
        <Tag key={`${e.name}|${e.type}`} title={e.type}>
          {e.name}
        </Tag>
      ))}
      {total > MAX_SHOWN ? (
        <Typography.Text type="secondary">and {total - MAX_SHOWN} more</Typography.Text>
      ) : null}
    </Flex>
  );
}

export function KnowledgeDiffCard({
  projectId,
  kvId,
  version,
}: {
  projectId: string;
  kvId: string;
  version: number;
}) {
  const diff = useCustom<KnowledgeDiff, ApiError>({
    url: diffPath(projectId, kvId),
    method: "get",
    queryOptions: { enabled: version > 1, retry: false },
  });
  const d = diff.result.data;
  if (version <= 1 || diff.query.error || !d || typeof d.added !== "number") return null;
  const documents = [
    ...(d.documents_added ?? []).map((t) => `+ ${t}`),
    ...(d.documents_removed ?? []).map((t) => `− ${t}`),
  ];
  return (
    <Card title={`Changes since v${d.against_version}`}>
      <Flex gap={32} wrap>
        <Statistic title="Entities added" prefix="+" value={d.added} />
        <Statistic title="Entities removed" prefix="−" value={d.removed} />
        <Statistic title="Unchanged" value={d.unchanged} />
        <Statistic title="Retyped" value={d.retyped} />
        <Statistic title="Relations added" prefix="+" value={d.relations_added} />
        <Statistic title="Relations removed" prefix="−" value={d.relations_removed} />
      </Flex>
      {documents.length > 0 ? (
        <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          Documents: {documents.join(" · ")}
        </Typography.Paragraph>
      ) : null}
      <Collapse
        ghost
        style={{ marginTop: 8 }}
        items={[
          {
            key: "added",
            label: `Added entities (${d.added})`,
            children: <Names items={d.added_entities ?? []} total={d.added} />,
          },
          {
            key: "removed",
            label: `Removed entities (${d.removed})`,
            children: <Names items={d.removed_entities ?? []} total={d.removed} />,
          },
        ]}
      />
    </Card>
  );
}
