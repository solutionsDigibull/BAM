"use client";

import { InboxOutlined } from "@ant-design/icons";
import { Alert, Button, Flex, Modal, Steps, Table, Tag, Typography, Upload } from "antd";
import { useState } from "react";

import { ApiErrorState, head, Meta } from "@/components/ui-kit";
import type { ImportReport, ImportRowError, StructureDiff } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { importWorkbook, workbookProblem } from "./masters-api";

const DIFF_COLOR: Record<StructureDiff["kind"], string> = {
  renamed: "orange",
  added: "blue",
  removed: "red",
  reordered: "purple",
};

const errorCount = (r: ImportReport) => r.tabs.reduce((n, t) => n + (t.errors?.length ?? 0), 0);

/** The 422 STRUCTURE_MISMATCH diff, verbatim from the server; such a workbook can't be applied. */
function StructureDiffTable({ error }: { error: ApiError }) {
  const diffs = (error.problem?.errors ?? []).flatMap((e, i) =>
    e.diff ? [{ key: i, path: e.path, message: e.message, ...e.diff }] : [],
  );
  return (
    <>
      <Alert
        type="error"
        showIcon
        message={error.problem?.title ?? "The workbook does not have the BAM 0.2 structure"}
        description={error.problem?.detail}
        style={{ marginBottom: 12 }}
      />
      <Table
        size="small"
        pagination={false}
        dataSource={diffs}
        columns={[
          { title: head("Tab"), dataIndex: "tab", key: "tab", width: 110 },
          {
            title: head("Expected"),
            key: "expected",
            render: (_, d) => d.expected ?? <Meta>—</Meta>,
          },
          { title: head("Actual"), key: "actual", render: (_, d) => d.actual ?? <Meta>—</Meta> },
          {
            title: head("Change"),
            key: "kind",
            width: 110,
            render: (_, d) => <Tag color={DIFF_COLOR[d.kind]}>{d.kind}</Tag>,
          },
        ]}
      />
    </>
  );
}

/** Any other refusal (413, 415, 403 FILETYPE_FROZEN, 422 MALWARE / ARCHIVE_LIMIT, 503): the server's words. */
function Refused({ error }: { error: ApiError }) {
  if (!error.problem?.title) return <ApiErrorState error={error} />;
  const details = (error.problem.errors ?? []).map((e) => e.message);
  return (
    <Alert
      type="error"
      showIcon
      message={error.problem.title}
      description={
        <>
          {error.problem.detail}
          {details.length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
              {details.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
        </>
      }
    />
  );
}

function Preview({ report }: { report: ImportReport }) {
  const rowErrors: (ImportRowError & { key: number })[] = report.tabs
    .flatMap((t) => t.errors ?? [])
    .map((e, key) => ({ ...e, key }));
  return (
    <>
      <Table
        size="small"
        pagination={false}
        rowKey="name"
        dataSource={report.tabs}
        columns={[
          { title: head("Tab"), dataIndex: "name", key: "name" },
          ...(["created", "updated", "unchanged", "missing"] as const).map((k) => ({
            title: head(k),
            key: k,
            render: (_: unknown, t: ImportReport["tabs"][number]) => t[k] ?? 0,
          })),
          {
            title: head("Errors"),
            key: "errors",
            render: (_, t) =>
              t.errors?.length ? <Tag color="red">{t.errors.length}</Tag> : <Meta>0</Meta>,
          },
        ]}
      />
      <Meta>
        &quot;Missing&quot; entries are kept: the import never deletes. File{" "}
        {report.sha256.slice(0, 19)}…
      </Meta>
      {rowErrors.length > 0 && (
        <Table
          size="small"
          style={{ marginTop: 16 }}
          pagination={rowErrors.length > 10 ? { pageSize: 10 } : false}
          dataSource={rowErrors}
          columns={[
            { title: head("Tab"), dataIndex: "tab", key: "tab", width: 110 },
            { title: head("Row"), key: "row", width: 70, render: (_, e) => e.row ?? "—" },
            { title: head("Column"), key: "column", width: 160, render: (_, e) => e.column ?? "—" },
            { title: head("Problem"), dataIndex: "message", key: "message" },
          ]}
        />
      )}
    </>
  );
}

/** Import (TD §9.2): validate-only first, a preview of what would change, then apply = upsert. */
export function ImportWizard({
  onClose,
  onApplied,
}: {
  onClose: () => void;
  onApplied: (report: ImportReport) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [pickProblem, setPickProblem] = useState<string | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (mode: "validate-only" | "upsert") => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await importWorkbook(file, mode);
      if (mode === "upsert" && result.applied) return onApplied(result);
      setReport(result); // a validation, or an upsert that met row errors after all
    } catch (e) {
      setReport(null);
      setError(e as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const mismatch = error?.problem?.code === "STRUCTURE_MISMATCH";
  const errors = report ? errorCount(report) : 0;
  const step = report || error ? 1 : 0;
  return (
    <Modal
      open
      width={820}
      title="Import the masters workbook"
      onCancel={onClose}
      maskClosable={false}
      footer={
        <Flex justify="space-between">
          <Button onClick={onClose}>Cancel</Button>
          {step === 0 ? (
            <Button
              type="primary"
              disabled={!file}
              loading={busy}
              onClick={() => run("validate-only")}
            >
              Validate
            </Button>
          ) : (
            <Flex gap={8}>
              <Button
                onClick={() => {
                  setReport(null);
                  setError(null);
                }}
              >
                Choose another file
              </Button>
              <Button
                type="primary"
                loading={busy}
                disabled={!report || errors > 0}
                onClick={() => run("upsert")}
              >
                Apply
              </Button>
            </Flex>
          )}
        </Flex>
      }
    >
      <Steps
        size="small"
        current={step}
        style={{ margin: "8px 0 20px" }}
        items={[{ title: "Choose file" }, { title: "Validate and preview" }, { title: "Apply" }]}
      />
      {step === 0 && (
        <>
          <Upload.Dragger
            accept=".xlsx"
            maxCount={1}
            beforeUpload={(f) => {
              const problem = workbookProblem(f);
              setPickProblem(problem);
              setFile(problem ? null : f);
              return false; // nothing is sent until Validate
            }}
            onRemove={() => setFile(null)}
            fileList={file ? [{ uid: "1", name: file.name, status: "done" }] : []}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p>Drop the BAM 0.2 masters workbook here, or click to choose it</p>
            <Typography.Text type="secondary">
              .xlsx · at most 15 MB · scanned for viruses
            </Typography.Text>
          </Upload.Dragger>
          {pickProblem && (
            <Alert type="error" showIcon message={pickProblem} style={{ marginTop: 12 }} />
          )}
        </>
      )}
      {error && (mismatch ? <StructureDiffTable error={error} /> : <Refused error={error} />)}
      {report && (
        <>
          {errors > 0 ? (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 12 }}
              message={`${errors} row error${errors > 1 ? "s" : ""}: fix the workbook and validate again. Nothing was imported.`}
            />
          ) : (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="The workbook is valid. Apply writes the created and updated entries."
            />
          )}
          <Preview report={report} />
        </>
      )}
    </Modal>
  );
}
