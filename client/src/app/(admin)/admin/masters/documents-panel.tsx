"use client";

// Master documents (TD §9.2, E3.11): context files an Admin attaches to a master. The server scans each
// upload, routes it by its Filetype row's default route and stores it; the Admin can override the route.

import { InboxOutlined, LockOutlined } from "@ant-design/icons";
import { useCustom, useList } from "@refinedev/core";
import {
  Alert,
  Button,
  ConfigProvider,
  Flex,
  List,
  Segmented,
  Select,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
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
import type { FiletypeInfo, MasterDocumentOut, MasterEntry, MasterType } from "@/contracts";
import { dataProvider, type ApiError } from "@/providers/data-provider";

import {
  DOCUMENT_POLL_MS,
  DOCUMENTS,
  entriesResource,
  FILETYPES_URL,
  isActive,
  MAX_DOCUMENT_BYTES,
  ROUTED_TYPES,
  uploadDocument,
  uploadMessage,
} from "./masters-api";

type Routed = (typeof ROUTED_TYPES)[number];
type Filter = "ALL" | Routed;
type Upload = { uid: string; name: string; state: "uploading" | "done" | "error"; message: string };

const TAB: Record<Routed, string> = {
  ORGANIZATION: "Organization",
  RULES: "Rules",
  AGENTS: "Agents",
  SKILLS: "Skills",
};

const SOURCE: Record<MasterDocumentOut["route_source"], { label: string; color: string }> = {
  DEFAULT_BY_FILETYPE: { label: "By filetype", color: "default" },
  ADMIN_OVERRIDE: { label: "ADMIN_OVERRIDE", color: "purple" },
  CLASSIFIER: { label: "Classifier", color: "cyan" },
};

function ScanChip({ doc }: { doc: MasterDocumentOut }) {
  if (doc.scan_status == null) return <Tag color="processing">Scanning</Tag>;
  if (doc.scan_status === "INFECTED")
    return (
      <Tag color="red" icon={<LockOutlined />}>
        Quarantined
      </Tag>
    );
  if (doc.scan_status === "SCAN_FAILED") return <Tag color="orange">Scan failed</Tag>;
  return <Tag color="green">Clean</Tag>;
}

const PARSE_COLOR = { PENDING: "default", PARSING: "processing", PARSED: "green", FAILED: "red" };

const extension = (name: string) => /\.[^.]+$/.exec(name.toLowerCase())?.[0] ?? "";

/** Master (and optional entry) for one document; PATCH with If-Match, only once the scan is clean. */
function RouteOverride({ doc, onSaved }: { doc: MasterDocumentOut; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [type, setType] = useState<Routed>(doc.master_type);
  const [entry, setEntry] = useState<string | null>(doc.master_entry_id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const entries = useList<MasterEntry, ApiError>({
    resource: entriesResource(type as MasterType),
    pagination: { mode: "off" },
    queryOptions: { enabled: editing },
  });
  const clean = doc.scan_status === "CLEAN";

  if (!editing)
    return (
      <Tooltip title={clean ? undefined : "Available once the scan is clean"}>
        <Button
          size="small"
          disabled={!clean}
          aria-label={`Change route of ${doc.title}`}
          onClick={() => setEditing(true)}
        >
          Change route
        </Button>
      </Tooltip>
    );

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await dataProvider.getOne({ resource: DOCUMENTS, id: doc.id }); // the ETag for If-Match
      await dataProvider.update({
        resource: DOCUMENTS,
        id: doc.id,
        variables: { master_type: type, master_entry_id: entry },
      });
      setEditing(false);
      onSaved();
    } catch (e) {
      const err = e as ApiError;
      setError(err.problem?.detail ?? err.problem?.title ?? err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Flex vertical gap={6} style={{ width: 230 }}>
      <Select<Routed>
        size="small"
        aria-label={`Master for ${doc.title}`}
        value={type}
        onChange={(t) => {
          setType(t);
          setEntry(null); // an entry must belong to the chosen master
        }}
        options={ROUTED_TYPES.map((t) => ({ value: t, label: TAB[t] }))}
      />
      <Select<string>
        size="small"
        allowClear
        aria-label={`Entry for ${doc.title}`}
        placeholder="Whole master (no entry)"
        loading={entries.query.isLoading}
        value={entry ?? undefined}
        onChange={(v) => setEntry(v ?? null)}
        options={(entries.result.data ?? []).map((e) => ({
          value: e.id,
          label: `${e.row_label} · ${e.stable_id}`,
        }))}
      />
      <Flex gap={6}>
        <Button size="small" type="primary" loading={saving} onClick={save}>
          Save route
        </Button>
        <Button size="small" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </Flex>
      {error && <Typography.Text type="danger">{error}</Typography.Text>}
    </Flex>
  );
}

export function DocumentsPanel() {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const list = useCustom<{ items: MasterDocumentOut[] }, ApiError>({
    url: DOCUMENTS,
    method: "get",
    config: { query: filter === "ALL" ? undefined : { master_type: filter } },
    queryOptions: {
      refetchInterval: (q) =>
        (q.state.data?.data.items ?? []).some((d) => isActive(d)) ? DOCUMENT_POLL_MS : false,
    },
  });
  const filetypes = useCustom<FiletypeInfo[], ApiError>({ url: FILETYPES_URL, method: "get" });
  const allowed = useMemo(
    () =>
      new Set(
        (Array.isArray(filetypes.result.data) ? filetypes.result.data : []) // `{}` while loading
          .filter((f) => f.mvp_enabled)
          .flatMap((f) => f.extensions),
      ),
    [filetypes.result.data],
  );
  const docs = list.result.data?.items ?? [];

  const track = (u: Upload) => setUploads((all) => [u, ...all.filter((x) => x.uid !== u.uid)]);

  const send = async (file: File & { uid: string }) => {
    const base = { uid: file.uid, name: file.name };
    // The server is authoritative; these only save a pointless upload.
    if (file.size > MAX_DOCUMENT_BYTES)
      return track({ ...base, state: "error", message: "Larger than 15 MB" });
    if (allowed.size && !allowed.has(extension(file.name)))
      return track({ ...base, state: "error", message: "File type not supported yet" });
    track({ ...base, state: "uploading", message: "Scanning…" });
    try {
      const doc = await uploadDocument(file);
      track({
        ...base,
        state: "done",
        message: `Routed to ${TAB[doc.master_type]} (${doc.intent_class})`,
      });
      void list.query.refetch();
    } catch (e) {
      track({ ...base, state: "error", message: uploadMessage(e as ApiError) });
    }
  };

  let table;
  if (list.query.isLoading) table = <LoadingState />;
  else if (list.query.isError)
    table = <ApiErrorState error={list.query.error} onRetry={() => list.query.refetch()} />;
  else if (!docs.length)
    table = (
      <EmptyState
        message={
          filter === "ALL"
            ? "No master documents yet. Drop files above to attach them."
            : `No documents routed to ${TAB[filter]} yet.`
        }
      />
    );
  else
    table = (
      <ConfigProvider theme={GRID_THEME}>
        <Table<MasterDocumentOut>
          rowKey="id"
          bordered={false}
          dataSource={docs}
          pagination={docs.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          scroll={{ x: 1150 }}
          style={GRID_STYLE}
          columns={[
            {
              title: head("File"),
              key: "title",
              render: (_, d) => (
                <Typography.Text ellipsis={{ tooltip: d.title }} style={{ maxWidth: 180 }}>
                  {d.title}
                </Typography.Text>
              ),
            },
            {
              title: head("Filetype"),
              key: "filetype",
              width: 120,
              render: (_, d) => <Meta>{d.filetype_row_label}</Meta>,
            },
            {
              title: head("Suggested route"),
              key: "intent",
              width: 170,
              render: (_, d) => <Meta>{d.intent_class}</Meta>,
            },
            {
              title: head("Master"),
              key: "master",
              width: 210,
              render: (_, d) => (
                <Flex gap={4} wrap>
                  <Tag color="blue">{TAB[d.master_type]}</Tag>
                  <Tag color={SOURCE[d.route_source].color}>{SOURCE[d.route_source].label}</Tag>
                </Flex>
              ),
            },
            {
              title: head("Scan"),
              key: "scan",
              width: 110,
              render: (_, d) => <ScanChip doc={d} />,
            },
            {
              title: head("Parse"),
              key: "parse",
              width: 100,
              render: (_, d) => <Tag color={PARSE_COLOR[d.parse_status]}>{d.parse_status}</Tag>,
            },
            {
              title: head("Route"),
              key: "override",
              fixed: "right", // the override control stays in view while the grid scrolls
              width: 270,
              render: (_, d) =>
                d.scan_status === "INFECTED" ? (
                  <Meta>Quarantined · no actions</Meta>
                ) : (
                  <RouteOverride doc={d} onSaved={() => void list.query.refetch()} />
                ),
            },
          ]}
        />
      </ConfigProvider>
    );

  return (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0" }}>
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          options={[
            { value: "ALL", label: "All masters" },
            ...ROUTED_TYPES.map((t) => ({ value: t, label: TAB[t] })),
          ]}
        />
        <Meta>
          {docs.length} document{docs.length === 1 ? "" : "s"}
          {docs.some((d) => isActive(d)) ? " · updating every 5 s" : ""}
        </Meta>
      </Flex>
      <Upload.Dragger
        multiple
        showUploadList={false}
        accept={[...allowed].join(",") || undefined}
        beforeUpload={(file) => {
          void send(file);
          return false; // sent by `send`, not by antd
        }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p>Drop context files for the masters here, or click to choose them</p>
        <Typography.Text type="secondary">
          At most 15 MB each · scanned for viruses · routed by file type, change the route below
        </Typography.Text>
      </Upload.Dragger>
      {uploads.length > 0 && (
        <List
          size="small"
          style={{ margin: "12px 0" }}
          dataSource={uploads}
          renderItem={(u) => (
            <List.Item>
              <Typography.Text>{u.name}</Typography.Text>
              {u.state === "error" ? (
                <Alert type="error" showIcon message={u.message} style={{ padding: "0 8px" }} />
              ) : (
                <Meta>{u.message}</Meta>
              )}
            </List.Item>
          )}
        />
      )}
      <div style={{ marginTop: 16 }}>{table}</div>
    </>
  );
}
