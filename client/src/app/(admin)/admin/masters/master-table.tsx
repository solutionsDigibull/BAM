"use client";

import { EditOutlined, PlusOutlined } from "@ant-design/icons";
import { useInvalidate, useList } from "@refinedev/core";
import { App, Button, ConfigProvider, Flex, Input, Table, Tag, Tooltip, Typography } from "antd";
import { useMemo, useState } from "react";

import {
  ApiErrorState,
  EmptyState,
  GRID_STYLE,
  GRID_THEME,
  head,
  LoadingState,
  Meta,
} from "@/components/ui-kit";
import type { MasterEntry, MasterTypeInfo } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { EntryDrawer } from "./entry-drawer";
import { cellText, emptyMessage, entriesResource, STATUS_COLOR } from "./masters-api";

const COLUMN_WIDTH = 220;
// ponytail: antd's virtual list only past this many rows; BAM workbooks hold tens of rows per tab.
const VIRTUAL_FROM = 200;

/** A row-2 header with its row-3 description as the tooltip (frozen BAM 0.2 layout). */
function ColumnHead({ header, description }: { header: string; description: string }) {
  return (
    <Tooltip title={description || undefined}>
      <span aria-label={description ? `${header}: ${description}` : header}>{head(header)}</span>
    </Tooltip>
  );
}

// Cells are text nodes, never HTML: a '<b onmouseover=…>' cell shows as those characters (E3.10-T08).
function Cell({ value }: { value: unknown }) {
  const text = cellText(value);
  return text ? (
    <Typography.Text ellipsis={{ tooltip: text }} style={{ maxWidth: COLUMN_WIDTH - 24 }}>
      {text}
    </Typography.Text>
  ) : (
    <Meta>—</Meta>
  );
}

export function MasterTable({ info }: { info: MasterTypeInfo }) {
  const { message } = App.useApp();
  const invalidate = useInvalidate();
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<{ id?: string } | null>(null);
  const { result, query } = useList<MasterEntry, ApiError>({
    resource: entriesResource(info.master_type),
    pagination: { mode: "off" },
  });
  const entries = useMemo(() => result.data ?? [], [result.data]);
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) =>
      [e.row_label, e.stable_id, ...Object.values(e.payload).map(cellText)].some((t) =>
        t.toLowerCase().includes(needle),
      ),
    );
  }, [entries, search]);

  const addRow = info.editable && (
    <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing({})}>
      Add Row
    </Button>
  );
  const drawer = editing && (
    <EntryDrawer
      info={info}
      entryId={editing.id}
      onClose={() => setEditing(null)}
      onSaved={(e) => {
        setEditing(null);
        void invalidate({
          resource: entriesResource(info.master_type),
          invalidates: ["list", "detail"],
        });
        message.success(
          editing.id ? `Saved ${e.row_label} (version ${e.version})` : `Created ${e.row_label}`,
        );
      }}
    />
  );

  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!entries.length)
    return (
      <>
        <EmptyState message={emptyMessage(info.tab)} action={addRow || undefined} />
        {drawer}
      </>
    );

  const width = 4 * 160 + info.columns.length * COLUMN_WIDTH;
  return (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0" }}>
        <Input
          allowClear
          placeholder={`Search ${info.tab}...`}
          aria-label={`Search ${info.tab}`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 340, maxWidth: "100%" }}
        />
        <Flex align="center" gap={16}>
          <Meta>
            {rows.length} of {entries.length} rows
          </Meta>
          {addRow}
        </Flex>
      </Flex>
      <ConfigProvider theme={GRID_THEME}>
        <Table<MasterEntry>
          rowKey="id"
          bordered={false}
          dataSource={rows}
          pagination={false}
          virtual={rows.length > VIRTUAL_FROM}
          scroll={{ x: width, y: rows.length > VIRTUAL_FROM ? 600 : undefined }}
          style={GRID_STYLE}
          locale={{
            emptyText: <EmptyState message={`No ${info.tab} entries match your search.`} />,
          }}
          columns={[
            {
              title: head("Name"),
              key: "row_label",
              fixed: "left",
              width: 120,
              render: (_, e) => <Meta>{e.row_label}</Meta>,
            },
            {
              title: head("Stable ID"),
              key: "stable_id",
              fixed: "left",
              width: 160,
              render: (_, e) => <Tag>{e.stable_id}</Tag>,
            },
            {
              title: head("Status"),
              key: "status",
              width: 140,
              render: (_, e) => <Tag color={STATUS_COLOR[e.status]}>{e.status}</Tag>,
            },
            ...info.columns.map((c) => ({
              title: <ColumnHead header={c.header} description={c.description} />,
              key: c.field,
              width: COLUMN_WIDTH,
              render: (_: unknown, e: MasterEntry) => <Cell value={e.payload[c.field]} />,
            })),
            ...(info.editable
              ? [
                  {
                    title: head("Actions"),
                    key: "actions",
                    fixed: "right" as const,
                    width: 100,
                    render: (_: unknown, e: MasterEntry) => (
                      <Button
                        size="small"
                        type="text"
                        icon={<EditOutlined />}
                        aria-label={`Edit ${e.row_label}`}
                        onClick={() => setEditing({ id: e.id })}
                      >
                        Edit
                      </Button>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </ConfigProvider>
      <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
        <Meta>
          {info.columns.length} columns · workbook order preserved · scroll horizontally for all
          fields
        </Meta>
        <Meta>Hover a column name for its description · Edit saves a new version</Meta>
      </Flex>
      {drawer}
    </>
  );
}
