"use client";

// Layout follows the UI team's admin masters reference (docs/reference/ui/README.md, admin-masters/): one tab
// per workbook sheet, the frozen BAM 0.2 columns with their row-3 descriptions, Filetype read-only.

import { DownloadOutlined, FileExcelOutlined, UploadOutlined } from "@ant-design/icons";
import { useCustom, useInvalidate } from "@refinedev/core";
import { Alert, App, Button, Flex, Table, Tabs, Typography } from "antd";
import { useState, type ReactNode } from "react";

import { ApiErrorState, LoadingState, MUTED } from "@/components/ui-kit";
import type { MasterType, MasterTypes, ReadmeTab } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { DocumentsPanel } from "./documents-panel";
import { FiletypeTable } from "./filetype-table";
import { MasterGridPanel } from "./master-grid-panel";
import { RulesEngine } from "./rules-engine";
import { ToolFactory } from "./tool-factory";
import { ImportWizard } from "./import-wizard";
import { download, entriesResource, LAYOUT_URL, SCREEN_TYPES } from "./masters-api";

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

export function MastersScreen({
  projectId,
  projectName,
}: {
  projectId?: string;
  projectName?: string;
}) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState<"export" | "template" | null>(null);
  const [downloadError, setDownloadError] = useState<ApiError | null>(null);
  const [importing, setImporting] = useState(false);
  const invalidate = useInvalidate();
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
  else if (query.isError || !layout?.types)
    body = (
      <ApiErrorState error={query.error ?? { statusCode: 500 }} onRetry={() => query.refetch()} />
    );
  else {
    const infoOf = (t: MasterType) => layout.types.find((i) => i.master_type === t);
    const [org, rules, skills, tools, agents] = (
      ["ORGANIZATION", "RULES", "SKILLS", "TOOLING", "AGENTS"] as const
    ).map(infoOf);
    const tab = (key: string, label: string, children: ReactNode) => ({ key, label, children });
    body = (
      <Tabs
        destroyOnHidden
        items={[
          tab("README", "README", <Readme readme={layout.readme} />),
          ...(org
            ? [tab(org.master_type, org.tab, <MasterGridPanel info={org} projectId={projectId} />)]
            : []),
          ...(rules
            ? [
                tab(
                  rules.master_type,
                  rules.tab,
                  <RulesEngine info={rules} projectId={projectId} />,
                ),
              ]
            : []),
          ...(skills
            ? [
                tab(
                  skills.master_type,
                  skills.tab,
                  <MasterGridPanel info={skills} projectId={projectId} singleClick duplicate />,
                ),
              ]
            : []),
          ...(tools || agents
            ? [
                tab(
                  "TOOLS",
                  "Tool Factory",
                  <ToolFactory tools={tools} agents={agents} projectId={projectId} />,
                ),
              ]
            : []),
          tab("DOCUMENTS", "Documents", <DocumentsPanel />),
          tab("FILETYPE", "Filetype", <FiletypeTable />),
        ]}
      />
    );
  }

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap gap={12} style={{ marginBottom: 20 }}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            {projectName ? `${projectName}: Masters` : "Masters"}
          </Typography.Title>
          <Typography.Text style={{ color: MUTED }}>
            The tenant&apos;s Masters 0.3: one tab per workbook sheet, columns frozen
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
            icon={<DownloadOutlined />}
            loading={busy === "export"}
            onClick={() => fetchFile("export")}
          >
            Export
          </Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => setImporting(true)}>
            Import workbook
          </Button>
        </Flex>
      </Flex>
      {downloadError && (
        <div style={{ marginBottom: 16 }}>
          <ApiErrorState error={downloadError} onRetry={() => setDownloadError(null)} />
        </div>
      )}
      {body}
      {importing && (
        <ImportWizard
          projectId={projectId}
          onClose={() => setImporting(false)}
          onApplied={(report) => {
            setImporting(false);
            const created = report.tabs.reduce((n, t) => n + (t.created ?? 0), 0);
            const updated = report.tabs.reduce((n, t) => n + (t.updated ?? 0), 0);
            message.success(`Imported: ${created} created, ${updated} updated`);
            for (const t of SCREEN_TYPES)
              void invalidate({ resource: entriesResource(t), invalidates: ["list"] });
          }}
        />
      )}
    </div>
  );
}
