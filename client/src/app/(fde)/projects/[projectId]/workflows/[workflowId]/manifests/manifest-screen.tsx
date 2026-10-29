"use client";

// The Manifest viewer (TD §9.3, E9.6): a Sub-Skill's Skill Manifest or the Workflow manifest (the
// E9.5 draft until certification), and the Workflow's schema.sql, all read only. Pick a version,
// compare two (Monaco's diff editor over key-sorted JSON, so only changed paths differ), copy the
// hash. There is no save: nothing here writes. Text is rendered through Monaco models, never HTML.
import { useCustom } from "@refinedev/core";
import { Alert, Segmented, Select, Space, Tabs, Tag, Typography } from "antd";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";

import { ApiErrorState, EmptyState, HashChip, LoadingState } from "@/components/ui-kit";
import type {
  ManifestVersionList,
  ManifestView,
  WorkbenchTree,
  WorkflowSchemaSql,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { treeUrl } from "../isatvon/workbench-api";
import {
  NOT_GENERATED,
  hashOf,
  manifestUrl,
  pretty,
  schemaSqlUrl,
  schemaUrl,
  versionsUrl,
  workbenchHref,
  type Subject,
} from "./manifests-api";

const MonacoViewer = dynamic(() => import("./monaco-viewer"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export function ManifestScreen({
  projectId,
  workflowId,
  subId,
}: {
  projectId: string;
  workflowId: string;
  subId: string | null;
}) {
  const [subject, setSubject] = useState<Subject>(subId ? "sub-skill" : "workflow");
  const [sub, setSub] = useState<string | null>(subId);
  const tree = useCustom<WorkbenchTree, ApiError>({
    url: treeUrl(projectId, workflowId),
    method: "get",
    queryOptions: { enabled: subject === "sub-skill" },
  });
  const subs = (tree.query.data?.data.macros ?? []).flatMap((m) => m.sub_skills ?? []);

  return (
    <div style={{ display: "grid", gap: 12 }} data-testid="manifest-screen">
      <Space wrap>
        <Segmented
          aria-label="Manifest of"
          value={subject}
          onChange={(v) => setSubject(v as Subject)}
          options={[
            { value: "sub-skill", label: "Sub-Skill" },
            { value: "workflow", label: "Workflow" },
          ]}
        />
        {subject === "sub-skill" ? (
          <Select
            aria-label="Sub-Skill"
            showSearch
            placeholder="Choose a Sub-Skill"
            style={{ minWidth: 320 }}
            value={sub ?? undefined}
            loading={tree.query.isLoading}
            optionFilterProp="label"
            onChange={(v: string) => setSub(v)}
            options={subs.map((s) => ({
              value: s.id,
              label: `${s.sub_skill.skill_metadata.skill_id} · ${s.sub_skill.skill_metadata.skill_name}`,
            }))}
          />
        ) : null}
      </Space>
      {subject === "sub-skill" && !sub ? (
        <EmptyState message="Choose a Sub-Skill to see its manifest" />
      ) : (
        <Subject
          key={`${subject}-${sub}`}
          projectId={projectId}
          workflowId={workflowId}
          subject={subject}
          sub={sub}
        />
      )}
    </div>
  );
}

function Subject({
  projectId,
  workflowId,
  subject,
  sub,
}: {
  projectId: string;
  workflowId: string;
  subject: Subject;
  sub: string | null;
}) {
  const versions = useCustom<ManifestVersionList, ApiError>({
    url: versionsUrl(projectId, workflowId, subject, sub),
    method: "get",
    queryOptions: { retry: false }, // a 404 (another tenant, not assigned) won't change
  });
  const items = versions.query.data?.data.items ?? [];
  const [buildNo, setBuildNo] = useState<number | null>(null);
  const [against, setAgainst] = useState<number | null>(null);
  const shown = buildNo ?? items[0]?.build_no ?? null;

  if (versions.query.isLoading) return <LoadingState />;
  if (versions.query.error)
    return <ApiErrorState error={versions.query.error} onRetry={() => versions.query.refetch()} />;
  if (shown === null)
    return (
      <EmptyState
        message={NOT_GENERATED}
        action={<Link href={workbenchHref(projectId, workflowId)}>Open the Workbench</Link>}
      />
    );

  const label = (n: number) => {
    const v = items.find((i) => i.build_no === n);
    return v
      ? `v${v.version} · build ${v.build_no}${v.is_frozen ? " · frozen" : ""}`
      : `build ${n}`;
  };
  return (
    <>
      <Space wrap>
        <Select
          aria-label="Version"
          style={{ minWidth: 220 }}
          value={shown}
          onChange={(v: number) => setBuildNo(v)}
          options={items.map((i) => ({ value: i.build_no, label: label(i.build_no) }))}
        />
        <Select
          aria-label="Compare with"
          allowClear
          placeholder="Compare with…"
          style={{ minWidth: 220 }}
          value={against ?? undefined}
          disabled={items.length < 2}
          onChange={(v: number | undefined) => setAgainst(v ?? null)}
          options={items
            .filter((i) => i.build_no !== shown)
            .map((i) => ({ value: i.build_no, label: label(i.build_no) }))}
        />
      </Space>
      <Tabs
        items={[
          {
            key: "manifest",
            label: "Manifest",
            children: (
              <ManifestPane
                projectId={projectId}
                workflowId={workflowId}
                subject={subject}
                sub={sub}
                buildNo={shown}
                against={against}
              />
            ),
          },
          {
            key: "schema-sql",
            label: "schema.sql",
            children: <SchemaSqlPane projectId={projectId} workflowId={workflowId} />,
          },
        ]}
      />
    </>
  );
}

function useManifest(
  projectId: string,
  workflowId: string,
  subject: Subject,
  sub: string | null,
  buildNo: number | null,
) {
  return useCustom<ManifestView, ApiError>({
    url: manifestUrl(projectId, workflowId, subject, sub, buildNo ?? 0),
    method: "get",
    queryOptions: { enabled: buildNo !== null, retry: false },
  });
}

function ManifestPane({
  projectId,
  workflowId,
  subject,
  sub,
  buildNo,
  against,
}: {
  projectId: string;
  workflowId: string;
  subject: Subject;
  sub: string | null;
  buildNo: number;
  against: number | null;
}) {
  const view = useManifest(projectId, workflowId, subject, sub, buildNo);
  const other = useManifest(projectId, workflowId, subject, sub, against);
  const schema = useCustom<Record<string, unknown>, ApiError>({
    url: schemaUrl(subject),
    method: "get",
    queryOptions: { staleTime: Infinity },
  });
  const data = view.query.data?.data;
  if (view.query.isLoading) return <LoadingState />;
  if (view.query.error)
    return <ApiErrorState error={view.query.error} onRetry={() => view.query.refetch()} />;
  if (!data) return <EmptyState message={NOT_GENERATED} />;
  const hash = hashOf(data);
  const original = against !== null ? other.query.data?.data : undefined;
  const schemaDoc = schema.query.data?.data;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <Space wrap data-testid="manifest-header">
        <Typography.Text strong>v{data.version}</Typography.Text>
        <HashChip value={hash} />
        {data.reused ? <Tag color="blue">Reused</Tag> : null}
        <Tag color={data.is_frozen ? "green" : "default"}>
          {data.is_frozen ? "Frozen" : "Draft"}
        </Tag>
        <Typography.Text type="secondary">{data.schema_version}</Typography.Text>
      </Space>
      {against !== null && other.query.isLoading ? <LoadingState /> : null}
      <MonacoViewer
        path={subject === "workflow" ? "workflow_manifest.json" : "skill_manifest.json"}
        language="json"
        value={pretty(data.manifest)}
        original={original ? pretty(original.manifest) : undefined}
        schema={
          schemaDoc
            ? { uri: String(schemaDoc.$id ?? schemaUrl(subject)), schema: schemaDoc }
            : undefined
        }
      />
    </div>
  );
}

function SchemaSqlPane({ projectId, workflowId }: { projectId: string; workflowId: string }) {
  const schema = useCustom<WorkflowSchemaSql, ApiError>({
    url: schemaSqlUrl(projectId, workflowId),
    method: "get",
    queryOptions: { retry: false },
  });
  if (schema.query.isLoading) return <LoadingState />;
  if (schema.query.error?.statusCode === 404) return <EmptyState message={NOT_GENERATED} />;
  if (schema.query.error)
    return <ApiErrorState error={schema.query.error} onRetry={() => schema.query.refetch()} />;
  const data = schema.query.data?.data;
  if (!data) return <EmptyState message={NOT_GENERATED} />;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <Space wrap data-testid="schema-sql-header">
        <Typography.Text strong>v{data.version}</Typography.Text>
        <Tag
          color={data.status === "PASSED" ? "green" : data.status === "FAILED" ? "red" : "default"}
        >
          {data.status}
        </Tag>
        <Tag>{data.source}</Tag>
        {data.sha256 ? <HashChip value={data.sha256} /> : null}
        <Typography.Text type="secondary">{(data.tables ?? []).join(", ")}</Typography.Text>
      </Space>
      {(data.findings ?? []).length ? (
        <Alert
          type="error"
          showIcon
          message="Findings"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {(data.findings ?? []).map((f, i) => (
                <li key={i}>
                  <Typography.Text code>{f.code}</Typography.Text> {f.message}
                </li>
              ))}
            </ul>
          }
        />
      ) : null}
      <MonacoViewer path="schema.sql" language="sql" value={data.sql ?? ""} />
    </div>
  );
}
