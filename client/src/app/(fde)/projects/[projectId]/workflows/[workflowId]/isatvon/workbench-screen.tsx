"use client";

// The ISATVON 1.4 workbench (TD §9.3, E8.7): the Macro → Sub-Skill breakdown SBAM extracted from the
// approved SOW, in a grid whose columns come only from the published metadata (GET /schemas/isatvon/1.4).
// The breakdown endpoints are #94's; until they land this screen is built against sbam_contracts.

import { useCustom, useCustomMutation, type BaseRecord } from "@refinedev/core";
import { Alert, App, Button, Flex, Input, Modal, Spin, Tooltip, Typography } from "antd";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiErrorState,
  EmptyState,
  LoadingState,
  LOCKED_MESSAGE,
  Meta,
  MUTED,
} from "@/components/ui-kit";
import type {
  IsatvonSchemaResponse,
  RuleViolation,
  SubSkillPatch,
  ValidationReport,
  WorkbenchSubSkill,
  WorkbenchTree,
} from "@/contracts";
import { download } from "@/app/(admin)/admin/masters/masters-api";
import type { ApiError } from "@/providers/data-provider";

import type { EditRequest, RowAction } from "./isatvon-grid";
import {
  applyReport,
  exportUrl,
  generateBlocked,
  generateUrl,
  isEditable,
  regenerateUrl,
  validateUrl,
  macroIds,
  metadataColumns,
  parseEdit,
  replaceSub,
  SCHEMA_URL,
  subSkills,
  subSkillUrl,
  toRows,
  treeUrl,
  withLocal,
  type LocalErrors,
} from "./workbench-api";

const IsatvonGrid = dynamic(() => import("./isatvon-grid"), {
  ssr: false,
  loading: () => <LoadingState />,
});

export const EMPTY_MESSAGE = "No Macro Skills yet";
const EXTRACT_POLL_MS = 5_000;
const REVIEW_POLL_MS = 30_000; // notices the Workflow leaving review (e.g. to BUILDING) while open
export const CONFLICT_TOAST = "Updated elsewhere: the row was reloaded with the latest version.";
const newKey = () => globalThis.crypto.randomUUID();

export function WorkbenchScreen({
  projectId,
  workflowId,
}: {
  projectId: string;
  workflowId: string;
}) {
  const { message, modal } = App.useApp();
  const schema = useCustom<IsatvonSchemaResponse, ApiError>({ url: SCHEMA_URL, method: "get" });
  const treeQuery = useCustom<WorkbenchTree, ApiError>({
    url: treeUrl(projectId, workflowId),
    method: "get",
    queryOptions: {
      refetchInterval: (q) => {
        const s = q.state.data?.data?.workflow_state;
        return s === "ISATVON_EXTRACTING"
          ? EXTRACT_POLL_MS
          : isEditable(s)
            ? REVIEW_POLL_MS
            : false;
      },
    },
  });
  const { mutateAsync } = useCustomMutation<WorkbenchSubSkill, ApiError>();
  const command = useCustomMutation<BaseRecord, ApiError>();
  // `useCustom` hands back `{}` while loading and the API may send null: never trust the shape.
  const server = treeQuery.result.data as unknown;
  // PATCH answers replace their row here; any refetch (a new `server`) supersedes them.
  const [patched, setPatched] = useState<{ base: unknown; tree: unknown } | null>(null);
  const current = patched && patched.base === server ? patched.tree : server;
  const [local, setLocal] = useState<LocalErrors>({});
  const [locked, setLocked] = useState(false);
  const tree = useMemo(() => withLocal(current, local), [current, local]);
  const latest = useRef({ server, current });
  useEffect(() => {
    latest.current = { server, current };
  }, [server, current]);
  const queues = useRef(new Map<string, Promise<void>>());

  const state = (current as Partial<WorkbenchTree> | null)?.workflow_state;
  const groups = schema.result.data?.grid?.column_groups;
  const ids = useMemo(() => macroIds(tree), [tree]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set()); // all expanded at first
  const expanded = useMemo(() => new Set(ids.filter((id) => !collapsed.has(id))), [ids, collapsed]);
  const canAct = isEditable(state) && !locked;
  const rows = useMemo(() => toRows(tree, expanded, canAct), [tree, expanded, canAct]);
  const subs = useMemo(() => subSkills(current), [current]);
  const invalid = subs.filter((s) => s.state === "INVALID").length;
  const blocked = locked ? LOCKED_MESSAGE : generateBlocked(current);
  const [busy, setBusy] = useState<string | null>(null);
  const [regen, setRegen] = useState<RowAction | null>(null);
  const [hint, setHint] = useState("");

  /** The answers every command shares: stale → reload, locked → read-only, else the problem detail. */
  const failed = async (err: ApiError) => {
    if (err.statusCode === 412) {
      message.warning(CONFLICT_TOAST);
      await treeQuery.query.refetch();
    } else if (err.statusCode === 423) setLocked(true);
    else message.error(err.message);
  };

  const send = async (
    name: string,
    url: string,
    method: "post" | "delete",
    values: object,
    headers: Record<string, string>,
  ) => {
    setBusy(name);
    try {
      return await command.mutateAsync({
        url,
        method,
        values,
        config: { headers },
        successNotification: false,
        errorNotification: false,
      });
    } catch (e) {
      await failed(e as ApiError);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const validate = async () => {
    const res = await send("validate", validateUrl(projectId, workflowId), "post", {}, {});
    if (!res) return;
    const report = res.data as unknown as ValidationReport;
    const next = applyReport(latest.current.current, report);
    latest.current.current = next;
    setPatched({ base: latest.current.server, tree: next });
    const bad = (report?.sub_skills ?? []).filter((s) => s.state !== "VALID").length;
    message.success(`Validated: ${bad} invalid`);
  };

  const exportExcel = async () => {
    setBusy("export");
    try {
      const name = await download(exportUrl(projectId, workflowId), `isatvon-${workflowId}.xlsx`);
      message.success(`Downloaded ${name}`);
    } catch (e) {
      message.error((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  };

  const generate = async () => {
    const res = await send(
      "generate",
      generateUrl(projectId, workflowId),
      "post",
      {},
      {
        "idempotency-key": newKey(),
      },
    );
    if (!res) return;
    message.success("Generation started");
    await treeQuery.query.refetch();
  };

  const regenerate = async () => {
    if (!regen) return;
    const body = hint.trim() ? { hint: hint.trim() } : {};
    const res = await send(
      "regenerate",
      regenerateUrl(projectId, workflowId, regen.kind, regen.id),
      "post",
      body,
      { "idempotency-key": newKey() },
    );
    setRegen(null);
    setHint("");
    if (!res) return;
    message.success(`Regenerating ${regen.label}…`);
    await treeQuery.query.refetch();
  };

  const onAction = (a: RowAction) => {
    if (a.action === "regenerate") return setRegen(a);
    modal.confirm({
      title: `Delete ${a.label}?`,
      content: "The Sub-Skill is removed from this Workflow's breakdown.",
      okText: "Delete",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await send(
          "delete",
          subSkillUrl(projectId, workflowId, a.id),
          "delete",
          {},
          {
            "if-match": `"${a.row.content_sha256}"`,
          },
        );
        if (!res) return;
        message.success(`Deleted ${a.label}`);
        await treeQuery.query.refetch();
      },
    });
  };

  /** Set one cell's edit errors; no messages clears them. */
  const mark = (id: string, path: string, messages: string[]) =>
    setLocal((all) => {
      const kept = (all[id] ?? []).filter((v) => v.path !== path);
      const added = messages.map((m): RuleViolation => ({ rule: 0, path, message: m }));
      return { ...all, [id]: [...kept, ...added] };
    });

  const save = async (id: string, path: string, value: unknown) => {
    // The row as it is now: an earlier edit to it may have changed its ETag.
    const row = subSkills(latest.current.current).find((s) => s.id === id);
    if (!row) return;
    try {
      const res = await mutateAsync({
        url: subSkillUrl(projectId, workflowId, id),
        method: "patch",
        values: { changes: { [path]: value } } satisfies SubSkillPatch,
        config: { headers: { "if-match": `"${row.content_sha256}"` } },
        successNotification: false,
        errorNotification: false,
      });
      const next = replaceSub(latest.current.current, res.data);
      latest.current.current = next;
      setPatched({ base: latest.current.server, tree: next });
      mark(id, path, []);
    } catch (e) {
      const err = e as ApiError;
      if (err.statusCode === 422) {
        const msgs = (err.problem?.errors ?? []).map((v) => v.message);
        mark(id, path, msgs.length ? msgs : [err.message]);
      } else if (err.statusCode === 412) {
        mark(id, path, []);
        message.warning(CONFLICT_TOAST);
        await treeQuery.query.refetch();
      } else if (err.statusCode === 423) {
        setLocked(true);
      } else {
        message.error(err.message);
      }
    }
  };

  // Client check first; then one PATCH at a time per row, so each carries the ETag the last one returned.
  const edit = ({ row, column, value }: EditRequest) => {
    const parsed = parseEdit(column, value);
    if ("error" in parsed) return mark(row.id, column.field_path, [parsed.error]);
    const queued = (queues.current.get(row.id) ?? Promise.resolve()).then(() =>
      save(row.id, column.field_path, parsed.value),
    );
    queues.current.set(row.id, queued);
  };

  const toggle = (id: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  let body;
  if (schema.query.isLoading || treeQuery.query.isLoading) body = <LoadingState />;
  else if (schema.query.isError)
    body = <ApiErrorState error={schema.query.error} onRetry={() => schema.query.refetch()} />;
  else if (treeQuery.query.isError)
    body = (
      <ApiErrorState error={treeQuery.query.error} onRetry={() => treeQuery.query.refetch()} />
    );
  else if (state === "ISATVON_EXTRACTING")
    body = (
      <Flex vertical align="center" gap={12} style={{ padding: 48 }}>
        <Spin />
        <Typography.Text>
          Extracting the ISATVON 1.4 breakdown from the approved SOW…
        </Typography.Text>
        <Meta>This page updates on its own.</Meta>
      </Flex>
    );
  else if (state === "EXTRACTION_FAILED")
    body = (
      <Alert
        type="error"
        showIcon
        message="The ISATVON extraction failed"
        description="Regenerate the breakdown, or check the SOW, then try again."
      />
    );
  else if (ids.length === 0) body = <EmptyState message={EMPTY_MESSAGE} />;
  else
    body = (
      <>
        {locked ? (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={LOCKED_MESSAGE} />
        ) : (
          !isEditable(state) && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`Read-only: the Workflow is ${state ?? "not in review"}.`}
            />
          )
        )}
        <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 12 }}>
          <Meta>
            {ids.length} Macro Skill{ids.length === 1 ? "" : "s"} · {subs.length} Sub-Skill
            {subs.length === 1 ? "" : "s"}
            {invalid ? ` · ${invalid} invalid` : ""}
          </Meta>
          <Flex gap={8} wrap align="center">
            <Button size="small" onClick={() => setCollapsed(new Set())}>
              Expand all
            </Button>
            <Button size="small" onClick={() => setCollapsed(new Set(ids))}>
              Collapse all
            </Button>
            {canAct && (
              <Button size="small" loading={busy === "validate"} onClick={validate}>
                Validate
              </Button>
            )}
            <Button size="small" loading={busy === "export"} onClick={exportExcel}>
              Export Excel
            </Button>
            {/* A disabled button swallows hover, so the reason sits on a wrapper. */}
            <Tooltip title={blocked ?? "Generate code for every VALID Sub-Skill"}>
              <span>
                <Button
                  type="primary"
                  size="small"
                  disabled={!!blocked}
                  loading={busy === "generate"}
                  onClick={generate}
                >
                  Generate
                </Button>
              </span>
            </Tooltip>
          </Flex>
        </Flex>
        {blocked && canAct && <Meta>Generate is off: {blocked}.</Meta>}
        <IsatvonGrid
          groups={groups ?? []}
          rows={rows}
          onToggle={toggle}
          onEdit={edit}
          onAction={onAction}
          state={state}
          locked={locked}
        />
        <Modal
          open={!!regen}
          title={regen ? `Regenerate ${regen.label}` : ""}
          okText="Regenerate"
          confirmLoading={busy === "regenerate"}
          onOk={regenerate}
          onCancel={() => {
            setRegen(null);
            setHint("");
          }}
          destroyOnHidden
        >
          <Typography.Paragraph type="secondary">
            SBAM extracts this {regen?.kind === "macro" ? "Macro Skill" : "Sub-Skill"} again from
            the approved SOW. Edits to it are replaced.
          </Typography.Paragraph>
          <Input.TextArea
            aria-label="Hint"
            placeholder="Optional hint, e.g. split the approval step out"
            rows={3}
            maxLength={2000}
            value={hint}
            onChange={(e) => setHint(e.target.value)}
          />
        </Modal>
        <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
          <Meta>{metadataColumns(groups).length} columns from the ISATVON 1.4 metadata</Meta>
          <Meta>
            {isEditable(state) && !locked ? "Double-click a cell to edit · " : ""}Hover a header for
            its path · a red cell shows the rule it breaks
          </Meta>
        </Flex>
      </>
    );

  return (
    <div>
      <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
        ISATVON Workbench
      </Typography.Title>
      <Typography.Text style={{ color: MUTED, display: "block", marginBottom: 20 }}>
        The Macro Skills and Sub-Skills SBAM extracted from the approved SOW (ISATVON 1.4)
      </Typography.Text>
      {body}
    </div>
  );
}
