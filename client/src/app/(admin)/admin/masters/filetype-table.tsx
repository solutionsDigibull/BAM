"use client";

import { LockOutlined } from "@ant-design/icons";
import { useCustom } from "@refinedev/core";
import { ConfigProvider, Flex, Segmented, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";

import {
  ApiErrorState,
  GRID_STYLE,
  GRID_THEME,
  head,
  LoadingState,
  Meta,
} from "@/components/ui-kit";
import type { FiletypeInfo } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { FILETYPES_URL } from "./masters-api";

type Tier = "all" | 1 | 2 | 3;

const text = (value: string, width = 260) => (
  <Typography.Text ellipsis={{ tooltip: value }} style={{ maxWidth: width }}>
    {value}
  </Typography.Text>
);

/** The 43 frozen Filetype rows: read-only for everyone, so no add or edit control exists here. */
export function FiletypeTable() {
  const [tier, setTier] = useState<Tier>("all");
  const { result, query } = useCustom<FiletypeInfo[], ApiError>({
    url: FILETYPES_URL,
    method: "get",
  });
  const all = useMemo(() => result.data ?? [], [result.data]);
  const rows = tier === "all" ? all : all.filter((f) => f.tier === tier);

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;

  const count = (t: Tier) => (t === "all" ? all.length : all.filter((f) => f.tier === t).length);
  return (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0" }}>
        <Segmented<Tier>
          value={tier}
          onChange={setTier}
          options={[
            { value: "all", label: `All file types (${count("all")})` },
            ...([1, 2, 3] as const).map((t) => ({ value: t, label: `Tier ${t} (${count(t)})` })),
          ]}
        />
        <Tag icon={<LockOutlined />}>Read-only · frozen BAM 0.2 catalog</Tag>
      </Flex>
      <ConfigProvider theme={GRID_THEME}>
        <Table<FiletypeInfo>
          rowKey="row_label"
          bordered={false}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 1500 }}
          style={GRID_STYLE}
          columns={[
            {
              title: head("Name"),
              key: "row_label",
              fixed: "left",
              width: 130,
              render: (_, f) => <Meta>{f.row_label}</Meta>,
            },
            {
              title: head("File format"),
              key: "format",
              width: 110,
              render: (_, f) => <Tag>{f.file_format}</Tag>,
            },
            { title: head("Tier"), key: "tier", width: 70, render: (_, f) => f.tier },
            { title: head("Purpose"), key: "purpose", render: (_, f) => text(f.purpose) },
            { title: head("RAG ingestion"), key: "rag", render: (_, f) => text(f.rag_ingestion) },
            { title: head("Example"), key: "example", render: (_, f) => text(f.example, 200) },
            {
              title: head("MVP loader"),
              key: "loader",
              width: 140,
              render: (_, f) =>
                f.mvp_enabled ? <Tag color="green">{f.loader}</Tag> : <Meta>not in MVP</Meta>,
            },
            {
              title: head("Default route"),
              key: "route",
              width: 160,
              render: (_, f) => <Tag>{f.default_route}</Tag>,
            },
          ]}
        />
      </ConfigProvider>
      <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
        <Meta>43 frozen types · only a super-admin can change them</Meta>
        <Meta>MVP loader and default route come from the Filetype configuration</Meta>
      </Flex>
    </>
  );
}
