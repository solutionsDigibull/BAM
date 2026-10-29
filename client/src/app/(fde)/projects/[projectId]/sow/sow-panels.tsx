"use client";

// SOW screen panels (E6.5): the citation detail, the version list, the diff drawer, the regenerate dialog
// and the save-conflict dialog. Each reads the API itself; the screen only says what to show.
import { useCustom } from "@refinedev/core";
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import { ApiErrorState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { SowCitationDetail, SowDiff, SowSectionDiff, SowVersionSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import type { CiteClick } from "./sow-editor";

import {
  MAX_HINT,
  SOURCE_DELETED,
  citationPath,
  citationSourceUrl,
  diffPath,
  where,
} from "./sow-api";

const CHANGE_COLOR: Record<string, string> = {
  ADDED: "green",
  REMOVED: "red",
  MODIFIED: "gold",
  UNCHANGED: "default",
};

// --- citation detail --------------------------------------------------------------------------------

export function CitationPanel({
  projectId,
  version,
  cite,
}: {
  projectId: string;
  version: number;
  cite: CiteClick | null;
}) {
  if (!cite)
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="Click a citation [n] in the SOW to see its source."
      />
    );
  if (cite.kind === "fde") return <FdeCitationDetail cite={cite} />;
  return <CitationDetail projectId={projectId} version={version} cite={cite} />;
}

/** An FDE-authored citation has no OKF source to fetch: show who wrote it and when (text only). */
function FdeCitationDetail({ cite }: { cite: Extract<CiteClick, { kind: "fde" }> }) {
  return (
    <div data-testid="citation-detail-fde">
      <Typography.Title level={5} style={{ marginTop: 0 }}>
        [{cite.number}] <Tag>Added by FDE</Tag>
      </Typography.Title>
      <Typography.Text type="secondary">
        {cite.author}, {cite.date}
      </Typography.Text>
      {cite.note ? (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
          {cite.note}
        </Typography.Paragraph>
      ) : null}
    </div>
  );
}

function CitationDetail({
  projectId,
  version,
  cite,
}: {
  projectId: string;
  version: number;
  cite: Extract<CiteClick, { kind: "okf" }>;
}) {
  const detail = useCustom<SowCitationDetail, ApiError>({
    url: citationPath(projectId, version, cite.okfRefId),
    method: "get",
  });
  if (detail.query.isLoading) return <LoadingState />;
  if (detail.query.error)
    return <ApiErrorState error={detail.query.error} onRetry={() => void detail.query.refetch()} />;
  const d = detail.result.data;
  if (!d) return null;
  return (
    <div data-testid="citation-detail">
      <Typography.Title level={5} style={{ marginTop: 0 }}>
        [{cite.number}] {d.title ?? <Tag color="red">{SOURCE_DELETED}</Tag>}
      </Typography.Title>
      <Typography.Text type="secondary">{where(d.location)}</Typography.Text>
      <Typography.Paragraph
        style={{ whiteSpace: "pre-wrap", marginTop: 8, maxHeight: 360, overflow: "auto" }}
        data-testid="citation-passage"
      >
        {d.passage}
        {d.truncated ? "…" : ""}
      </Typography.Paragraph>
      {d.source_available ? (
        <Button href={citationSourceUrl(projectId, version, cite.okfRefId)} download>
          Open source
        </Button>
      ) : null}
    </div>
  );
}

// --- version list (virtualized: projects can reach hundreds of versions) ----------------------------

export function VersionsPanel({
  versions,
  shown,
  onOpen,
}: {
  versions: SowVersionSummary[];
  shown: number | undefined;
  onOpen: (version: number) => void;
}) {
  return (
    <Table<SowVersionSummary>
      data-testid="sow-versions"
      size="small"
      rowKey="id"
      virtual
      scroll={{ y: 420 }}
      pagination={false}
      dataSource={versions}
      onRow={(row) => ({
        onClick: () => onOpen(row.version),
        style: { cursor: "pointer", background: row.version === shown ? "#e6f4ff" : undefined },
      })}
      columns={[
        { title: "Version", dataIndex: "version", width: 80, render: (v: number) => `v${v}` },
        {
          title: "Status",
          dataIndex: "status",
          width: 130,
          render: (s: string) => <StatusBadge status={s} />,
        },
        { title: "Source", dataIndex: "source", width: 100 },
        {
          title: "Saved",
          dataIndex: "created_at",
          render: (t: string) => new Date(t).toLocaleString(),
        },
      ]}
    />
  );
}

// --- diff --------------------------------------------------------------------------------------------

export function DiffDrawer({
  projectId,
  versions,
  open,
  onClose,
}: {
  projectId: string;
  versions: SowVersionSummary[];
  open: boolean;
  onClose: () => void;
}) {
  const numbers = versions.map((v) => v.version).sort((a, b) => a - b);
  const [from, setFrom] = useState<number | undefined>(numbers[0]);
  const [to, setTo] = useState<number | undefined>(numbers[numbers.length - 1]);
  const options = numbers.map((n) => ({ value: n, label: `v${n}` }));
  return (
    <Drawer title="Compare versions" open={open} onClose={onClose} width={520} destroyOnHidden>
      <Space style={{ marginBottom: 16 }}>
        <Select
          aria-label="From version"
          style={{ width: 100 }}
          value={from}
          options={options}
          onChange={setFrom}
        />
        <span>→</span>
        <Select
          aria-label="To version"
          style={{ width: 100 }}
          value={to}
          options={options}
          onChange={setTo}
        />
      </Space>
      {from && to ? <DiffList projectId={projectId} from={from} to={to} /> : null}
    </Drawer>
  );
}

export function DiffList({ projectId, from, to }: { projectId: string; from: number; to: number }) {
  const diff = useCustom<SowDiff, ApiError>({ url: diffPath(projectId, from, to), method: "get" });
  if (diff.query.isLoading) return <LoadingState />;
  if (diff.query.error)
    return <ApiErrorState error={diff.query.error} onRetry={() => void diff.query.refetch()} />;
  return (
    <div data-testid="sow-diff">
      <SectionChanges sections={diff.result.data?.sections ?? []} />
    </div>
  );
}

/** Section badges with their changed items: the diff drawer and the impact preview (E7.3) share them. */
export function SectionChanges({ sections }: { sections: SowSectionDiff[] }) {
  return (
    <>
      {sections.map((s) => (
        <div key={s.section_id} data-section={s.section_id} style={{ marginBottom: 8 }}>
          <Tag color={CHANGE_COLOR[s.change]} data-change={s.change}>
            {s.change}
          </Tag>
          <Typography.Text strong>{s.section_id.replace(/_/g, " ")}</Typography.Text>
          {s.items?.length ? (
            <div style={{ marginLeft: 24 }}>
              {(s.items ?? []).map((i) => (
                <Tag key={i.id} color={CHANGE_COLOR[i.change]} data-item={i.id}>
                  {i.id} · {i.change}
                </Tag>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </>
  );
}

// --- regenerate ------------------------------------------------------------------------------------

export function RegenerateModal({
  open,
  busy,
  onCancel,
  onRegenerate,
}: {
  open: boolean;
  busy: boolean;
  onCancel: () => void;
  onRegenerate: (hint: string) => void;
}) {
  const [hint, setHint] = useState("");
  return (
    <Modal
      title="Regenerate the SOW"
      open={open}
      okText="Regenerate"
      confirmLoading={busy}
      onCancel={onCancel}
      onOk={() => onRegenerate(hint)}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">
        SBAM writes a new version from the latest knowledge. Tell it what to change (optional).
      </Typography.Paragraph>
      <Input.TextArea
        aria-label="Hint"
        rows={4}
        maxLength={MAX_HINT}
        showCount
        value={hint}
        onChange={(e) => setHint(e.target.value)}
        placeholder="e.g. Add a quality-inspection module"
      />
    </Modal>
  );
}

// --- save conflict (409): what changed meanwhile; the draft is never thrown away silently -----------

export function ConflictModal({
  projectId,
  base,
  latest,
  open,
  onCopy,
  onReload,
  onClose,
}: {
  projectId: string;
  base: number;
  latest: number;
  open: boolean;
  onCopy: () => void;
  onReload: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      title="Someone saved a newer version"
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="copy" onClick={onCopy}>
          Copy my edits
        </Button>,
        <Button key="reload" type="primary" onClick={onReload}>
          Reload v{latest}
        </Button>,
      ]}
      destroyOnHidden
    >
      <Alert
        type="warning"
        showIcon
        message={`Your edits are based on v${base}; v${latest} is now current. Nothing was overwritten.`}
        style={{ marginBottom: 12 }}
      />
      <Typography.Text strong>What changed in v{latest}:</Typography.Text>
      <DiffList projectId={projectId} from={base} to={latest} />
    </Modal>
  );
}
