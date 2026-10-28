"use client";

// The Sub-Skill drawer (E8.8, TD §9.3): one Sub-Skill's full ISATVON 1.4 definition, its gates in the
// expression editor, the SOW features it cites, its validation errors (the server's, which stay the
// authority) and its edit history. Edits go through the same PATCH as the grid (If-Match). On 412 the
// row is reloaded and the user's edit is kept beside the latest value, never thrown away silently.

import { useCustom, useCustomMutation } from "@refinedev/core";
import { Alert, Button, Drawer, Flex, Input, List, Space, Tabs, Typography } from "antd";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useMemo, useState } from "react";

import { ApiErrorState, LoadingState, Meta, StatusBadge } from "@/components/ui-kit";
import type { SubSkillEditHistory, SubSkillPatch, WorkbenchSubSkill } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { gateDiagnostics, gateScope } from "./gate-expr";
import { editsUrl, subSkillUrl, valueAt } from "./workbench-api";

const GateEditor = dynamic(() => import("./gate-editor"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const BLOCKS: { key: string; title: string }[] = [
  { key: "i_instructions", title: "I — Instructions" },
  { key: "s_source", title: "S — Source" },
  { key: "a_automation", title: "A — Automation" },
  { key: "t_tech_stack", title: "T — Tech stack" },
  { key: "v_variables", title: "V — Variables" },
  { key: "o_outcome", title: "O — Outcome" },
  { key: "n_notification", title: "N — Next and notification" },
];
export const GATES = ["success_gate", "no_op_condition", "escalation_condition"] as const;
export const KEPT_EDIT =
  "Your edit was not saved: the Sub-Skill changed elsewhere. It is kept here.";
const DEF = "isatvon_definition";

type Conflict = { path: string; mine: unknown };

const show = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "");

export type SubSkillDrawerProps = {
  projectId: string;
  workflowId: string;
  row: WorkbenchSubSkill | null;
  editable: boolean;
  cycle: string | null;
  onSaved: (row: WorkbenchSubSkill) => void;
  onStale: () => Promise<unknown>;
  onLocked: () => void;
  onClose: () => void;
};

export function SubSkillDrawer(props: SubSkillDrawerProps) {
  const { row, onClose } = props;
  const id = String(valueAt(row?.sub_skill, "skill_metadata.skill_id") ?? "");
  const name = String(valueAt(row?.sub_skill, "skill_metadata.skill_name") ?? "");
  return (
    <Drawer
      open={!!row}
      onClose={onClose}
      width={720}
      destroyOnHidden
      title={
        <Space>
          <span>{id}</span>
          <Meta>{name}</Meta>
          {row && <StatusBadge status={row.state} />}
        </Space>
      }
    >
      {row && <DrawerBody key={row.id} {...props} row={row} />}
    </Drawer>
  );
}

function DrawerBody({
  projectId,
  workflowId,
  row,
  editable,
  cycle,
  onSaved,
  onStale,
  onLocked,
}: SubSkillDrawerProps & { row: WorkbenchSubSkill }) {
  const definition = useMemo(
    () => (valueAt(row.sub_skill, DEF) ?? {}) as Record<string, unknown>,
    [row.sub_skill],
  );
  const scope = useMemo(
    () => gateScope(definition as Parameters<typeof gateScope>[0]),
    [definition],
  );
  const { mutateAsync, mutation } = useCustomMutation<WorkbenchSubSkill, ApiError>();
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("isatvon");

  const save = async (path: string, value: unknown) => {
    setError(null);
    try {
      const res = await mutateAsync({
        url: subSkillUrl(projectId, workflowId, row.id),
        method: "patch",
        values: { changes: { [path]: value } } satisfies SubSkillPatch,
        config: { headers: { "if-match": `"${row.content_sha256}"` } },
        successNotification: false,
        errorNotification: false,
      });
      setConflict(null);
      onSaved(res.data);
      return true;
    } catch (e) {
      const err = e as ApiError;
      if (err.statusCode === 412) {
        setConflict({ path, mine: value });
        await onStale(); // the drawer re-renders on the latest row
      } else if (err.statusCode === 423) onLocked();
      else {
        const lines = (err.problem?.errors ?? []).map((v) => `${v.path}: ${v.message}`);
        setError(lines.length ? lines.join("\n") : err.message);
      }
      return false;
    }
  };

  const errors = row.validation_errors ?? [];
  return (
    <Flex vertical gap={12}>
      {cycle && <Alert type="error" showIcon data-testid="drawer-cycle" message={cycle} />}
      {errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          data-testid="drawer-errors"
          message={`${errors.length} validation error${errors.length === 1 ? "" : "s"}`}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {errors.map((v) => (
                <li key={`${v.path}:${v.message}`}>
                  <code>{v.path}</code> — {v.message}
                  {v.code ? <Meta> ({v.code})</Meta> : null}
                </li>
              ))}
            </ul>
          }
        />
      )}
      {conflict && (
        <Alert
          type="warning"
          showIcon
          data-testid="drawer-conflict"
          message={KEPT_EDIT}
          closable
          onClose={() => setConflict(null)}
          description={
            <Flex gap={12} wrap>
              <div style={{ flex: 1, minWidth: 240 }}>
                <Typography.Text strong>Your edit</Typography.Text>
                <pre data-testid="conflict-mine">{show(conflict.mine)}</pre>
              </div>
              <div style={{ flex: 1, minWidth: 240 }}>
                <Typography.Text strong>Now on the server</Typography.Text>
                <pre data-testid="conflict-theirs">
                  {show(valueAt(row.sub_skill, conflict.path))}
                </pre>
              </div>
            </Flex>
          }
        />
      )}
      {error && (
        <Alert type="error" showIcon message="Not saved" description={<pre>{error}</pre>} />
      )}
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: "isatvon",
            label: "ISATVON",
            children: (
              <Flex vertical gap={12}>
                {BLOCKS.map((b) => (
                  <Block
                    key={b.key}
                    title={b.title}
                    value={definition[b.key]}
                    editable={editable}
                    busy={mutation.isPending}
                    onSave={(v) => save(`${DEF}.${b.key}`, v)}
                  />
                ))}
              </Flex>
            ),
          },
          {
            key: "gates",
            label: "Gates",
            children: (
              <Flex vertical gap={16}>
                {GATES.map((g) => (
                  <Gate
                    key={`${g}:${row.content_sha256}`}
                    name={g}
                    value={String(valueAt(definition, `v_variables.${g}`) ?? "")}
                    scope={scope}
                    editable={editable}
                    busy={mutation.isPending}
                    onSave={(v) => save(`${DEF}.v_variables.${g}`, v)}
                  />
                ))}
                <Meta>Names you can use: {scope.join(", ")}</Meta>
              </Flex>
            ),
          },
          {
            key: "citations",
            label: `Citations (${row.sow_refs?.length ?? 0})`,
            children: (
              <List
                data-testid="drawer-citations"
                locale={{ emptyText: "This Sub-Skill cites no SOW feature." }}
                dataSource={row.sow_refs ?? []}
                renderItem={(ref) => (
                  <List.Item>
                    <Link href={`/projects/${projectId}/sow#${encodeURIComponent(ref)}`}>
                      {ref}
                    </Link>
                  </List.Item>
                )}
              />
            ),
          },
          {
            key: "history",
            label: "History",
            children: tab === "history" && (
              <History projectId={projectId} workflowId={workflowId} id={row.id} />
            ),
          },
        ]}
      />
    </Flex>
  );
}

function Block({
  title,
  value,
  editable,
  busy,
  onSave,
}: {
  title: string;
  value: unknown;
  editable: boolean;
  busy: boolean;
  onSave: (value: unknown) => Promise<boolean>;
}) {
  const [text, setText] = useState<string | null>(null);
  const [bad, setBad] = useState<string | null>(null);
  const editing = text !== null;
  const submit = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text ?? "");
    } catch {
      setBad("Not valid JSON");
      return;
    }
    setBad(null);
    if (await onSave(parsed)) setText(null);
  };
  return (
    <div data-testid={`block-${title.charAt(0)}`}>
      <Flex justify="space-between" align="center">
        <Typography.Text strong>{title}</Typography.Text>
        {editable && !editing && (
          <Button size="small" onClick={() => setText(JSON.stringify(value, null, 2))}>
            Edit
          </Button>
        )}
      </Flex>
      {editing ? (
        <Flex vertical gap={6}>
          <Input.TextArea
            aria-label={`${title} JSON`}
            autoSize={{ minRows: 4, maxRows: 20 }}
            value={text}
            onChange={(e) => setText(e.target.value)}
            status={bad ? "error" : undefined}
          />
          {bad && <Typography.Text type="danger">{bad}</Typography.Text>}
          <Space>
            <Button size="small" type="primary" loading={busy} onClick={submit}>
              Save
            </Button>
            <Button size="small" onClick={() => setText(null)}>
              Cancel
            </Button>
          </Space>
        </Flex>
      ) : (
        <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>{show(value)}</pre>
      )}
    </div>
  );
}

function Gate({
  name,
  value,
  scope,
  editable,
  busy,
  onSave,
}: {
  name: string;
  value: string;
  scope: readonly string[];
  editable: boolean;
  busy: boolean;
  onSave: (value: string) => Promise<boolean>;
}) {
  const [text, setText] = useState(value);
  const diagnostics = useMemo(() => gateDiagnostics(text, scope), [text, scope]);
  const errors = diagnostics.filter((d) => d.severity === "error");
  return (
    <Flex vertical gap={6}>
      <Typography.Text strong>{name}</Typography.Text>
      <GateEditor value={text} scope={scope} readOnly={!editable} label={name} onChange={setText} />
      <div data-testid={`gate-problems-${name}`}>
        {diagnostics.map((d) => (
          <Typography.Text
            key={`${d.from}:${d.message}`}
            type={d.severity === "error" ? "danger" : "warning"}
            style={{ display: "block" }}
          >
            {d.message}
          </Typography.Text>
        ))}
      </div>
      {editable && (
        <Space>
          <Button
            size="small"
            type="primary"
            disabled={errors.length > 0 || text === value}
            loading={busy}
            onClick={() => onSave(text)}
          >
            Save {name}
          </Button>
        </Space>
      )}
    </Flex>
  );
}

function History({
  projectId,
  workflowId,
  id,
}: {
  projectId: string;
  workflowId: string;
  id: string;
}) {
  const history = useCustom<SubSkillEditHistory, ApiError>({
    url: editsUrl(projectId, workflowId, id),
    method: "get",
  });
  if (history.query.isLoading) return <LoadingState />;
  if (history.query.isError)
    return <ApiErrorState error={history.query.error} onRetry={() => history.query.refetch()} />;
  const edits = history.result.data?.edits ?? [];
  return (
    <List
      data-testid="drawer-history"
      locale={{ emptyText: "No edits yet: this is the extracted Sub-Skill." }}
      dataSource={[...edits].reverse()}
      renderItem={(e) => (
        <List.Item>
          <Flex vertical>
            <Typography.Text>
              <code>{e.path}</code>
            </Typography.Text>
            <Meta>
              {e.by} · {e.at}
            </Meta>
            <Typography.Text type="secondary">
              {show(e.old)} → {show(e.new)}
            </Typography.Text>
          </Flex>
        </List.Item>
      )}
    />
  );
}
