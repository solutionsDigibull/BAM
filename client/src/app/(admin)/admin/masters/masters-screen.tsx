"use client";

// Layout follows the UI team's admin masters reference (docs/reference/ui/README.md, admin-masters/): one tab
// per workbook sheet, the frozen BAM 0.2 columns with their row-3 descriptions, Filetype read-only.

import { DownloadOutlined, FileExcelOutlined } from "@ant-design/icons";
import { useCustom } from "@refinedev/core";
import { Alert, App, Button, Flex, Table, Tabs, Typography } from "antd";
import { useState } from "react";

import { ApiErrorState, LoadingState, MUTED } from "@/components/ui-kit";
import type { MasterTypes, ReadmeTab } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { FiletypeTable } from "./filetype-table";
import { MasterTable } from "./master-table";
import { download, LAYOUT_URL, SCREEN_TYPES } from "./masters-api";

function Readme({ readme }: { readme: ReadmeTab }) {
  const rows = readme.rows.map((cells, i) => ({ key: i, cells }));
  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ margin: "16px 0" }}
        message="Structure accepted · BAM 0.2 columns are frozen. Row content remains Proposed or Illustrative."
      />
      <Typography.Title level={2} style={{ fontSize: 16 }}>
        {readme.title}
      </Typography.Title>
      <Table
        size="small"
        showHeader={false}
        pagination={false}
        dataSource={rows}
        columns={[0, 1, 2, 3].map((i) => ({
          key: i,
          render: (_: unknown, r: (typeof rows)[number]) => r.cells?.[i] ?? "",
        }))}
      />
    </>
  );
}

export function MastersScreen() {
  const { message } = App.useApp();
  const [busy, setBusy] = useState<"export" | "template" | null>(null);
  const [downloadError, setDownloadError] = useState<ApiError | null>(null);
  const { result, query } = useCustom<MasterTypes, ApiError>({ url: LAYOUT_URL, method: "get" });
  const layout = result.data;

  const fetchFile = async (kind: "export" | "template") => {
    setBusy(kind);
    setDownloadError(null);
    try {
      const fallback = kind === "export" ? "BAM_masters.xlsx" : "BAM_masters_template.xlsx";
      const name = await download(`admin/masters/${kind}`, fallback);
      message.success(`Downloaded ${name}`);
    } catch (e) {
      setDownloadError(e as ApiError);
    } finally {
      setBusy(null);
    }
  };

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (query.isError || !layout)
    body = (
      <ApiErrorState error={query.error ?? { statusCode: 500 }} onRetry={() => query.refetch()} />
    );
  else {
    const types = SCREEN_TYPES.map((t) => layout.types.find((i) => i.master_type === t)!);
    body = (
      <Tabs
        destroyOnHidden
        items={[
          { key: "README", label: "README", children: <Readme readme={layout.readme} /> },
          ...types.map((info) => ({
            key: info.master_type,
            label: info.tab,
            children: <MasterTable info={info} />,
          })),
          { key: "FILETYPE", label: "Filetype", children: <FiletypeTable /> },
        ]}
      />
    );
  }

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap gap={12} style={{ marginBottom: 20 }}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            Masters
          </Typography.Title>
          <Typography.Text style={{ color: MUTED }}>
            The tenant&apos;s BAM 0.2 masters: one tab per workbook sheet, columns frozen
          </Typography.Text>
        </div>
        <Flex gap={8} wrap>
          <Button
            icon={<FileExcelOutlined />}
            loading={busy === "template"}
            onClick={() => fetchFile("template")}
          >
            Download template
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={busy === "export"}
            onClick={() => fetchFile("export")}
          >
            Export
          </Button>
        </Flex>
      </Flex>
      {downloadError && (
        <div style={{ marginBottom: 16 }}>
          <ApiErrorState error={downloadError} onRetry={() => setDownloadError(null)} />
        </div>
      )}
      {body}
    </div>
  );
}
