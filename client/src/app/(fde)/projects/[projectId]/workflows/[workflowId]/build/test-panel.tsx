"use client";

// Test panel (TD §9.3, E13.7): a Sub-Skill's loop test cases and what the harness did with them.
// Cases are edited with If-Match (the dataset's sha256); the server keeps at least three, refuses
// a certified or frozen Sub-Skill (423) and validates the input (422). Every trace and output is
// shown as text: tool outputs come from generated code.

import { useCustom, useCustomMutation, type BaseRecord } from "@refinedev/core";
import {
  Alert,
  App,
  Button,
  Collapse,
  Drawer,
  Empty,
  Flex,
  Input,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import { ApiErrorState, Meta } from "@/components/ui-kit";
import type {
  BuildRow,
  HarnessTrace,
  PassRecord,
  TestCaseOut,
  TestRunOut,
  TestRunsResponse,
  TestRunTraces,
  TestsResponse,
} from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  canDelete,
  caseResult,
  casesOf,
  casesUrl,
  caseUrl,
  datasetOf,
  fieldProblems,
  FROZEN_MESSAGE,
  inputProblems,
  inputSchemaOf,
  isReadOnly,
  jsonText,
  MIN_CASES_MESSAGE,
  NO_TRACE_MESSAGE,
  NOT_RUN_MESSAGE,
  runsOf,
  runsUrl,
  STALE_MESSAGE,
  testsUrl,
  tracesOf,
  tracesUrl,
  writeError,
  type CaseResult,
  type FieldProblem,
} from "./test-panel-api";

type Terminal = TestCaseOut["expected_terminal"];
const TERMINALS: Terminal[] = ["SUCCESS", "ESCALATED", "NO_OP"];
const RESULT: Record<CaseResult, { color: string; text: string }> = {
  PASSED: { color: "success", text: "Passed" },
  FAILED: { color: "error", text: "Failed" },
  NOT_RUN: { color: "default", text: "Not run" },
};

/** JSON as text in a <pre>; past 2 KB it stays collapsed until asked for. */
export function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const { text, long } = jsonText(value);
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Meta>{label}</Meta>
      {long && !open ? (
        <Button size="small" onClick={() => setOpen(true)}>
          Show {label.toLowerCase()} ({Math.round(text.length / 1024)} KB)
        </Button>
      ) : (
        <pre
          aria-label={label}
          style={{ margin: "4px 0 0", maxHeight: 320, overflow: "auto", whiteSpace: "pre-wrap" }}
        >
          {text}
        </pre>
      )}
    </div>
  );
}

function PassRows({ passes }: { passes: PassRecord[] }) {
  return (
    <Table<PassRecord>
      size="small"
      pagination={false}
      rowKey={(p) => p.pass}
      dataSource={passes}
      aria-label="Handoff rows"
      columns={[
        { title: "Pass", dataIndex: "pass", width: 64 },
        { title: "ui_update", dataIndex: "ui_update" },
        { title: "Action", width: 90, render: (_, p) => `${p.action_ms} ms` },
        { title: "Check", width: 90, render: (_, p) => `${p.check_ms} ms` },
        {
          title: "Gates",
          width: 160,
          render: (_, p) =>
            p.gate_eval.success ? "success" : p.gate_eval.escalation ? "escalation" : "continue",
        },
      ]}
    />
  );
}

/** One case's trace, pass by pass: fresh state, action and check outputs, ui_update, durations. */
export function CaseTrace({ trace }: { trace: HarnessTrace }) {
  return (
    <Flex vertical gap={12}>
      <Meta>
        {trace.iterations} pass{trace.iterations === 1 ? "" : "es"} · {trace.handoff_rows} handoff
        row{trace.handoff_rows === 1 ? "" : "s"}
      </Meta>
      {trace.error && (
        <Alert
          type="error"
          showIcon
          message={`${trace.error.category} in the ${trace.error.step} of pass ${trace.error.pass}`}
          description={<span style={{ whiteSpace: "pre-wrap" }}>{trace.error.message}</span>}
        />
      )}
      <PassRows passes={trace.passes} />
      {trace.passes.map((p) => (
        <section key={p.pass} aria-label={`Pass ${p.pass}`}>
          <Typography.Text strong>{p.ui_update}</Typography.Text>
          <Flex vertical gap={8} style={{ marginTop: 8 }}>
            <JsonBlock label="Fresh state" value={p.fresh_state} />
            <JsonBlock label="Action output" value={p.action_output} />
            <JsonBlock label="Check output" value={p.check_output} />
          </Flex>
        </section>
      ))}
    </Flex>
  );
}

function TraceTab({
  projectId,
  workflowId,
  subId,
  runs,
}: {
  projectId: string;
  workflowId: string;
  subId: string;
  runs: TestRunOut[];
}) {
  const [picked, setRunId] = useState<string>();
  const runId = picked ?? runs[0]?.id; // the newest run until the FDE picks another
  const { result, query } = useCustom<TestRunTraces, ApiError>({
    url: tracesUrl(projectId, workflowId, subId, runId ?? ""),
    method: "get",
    queryOptions: { enabled: !!runId, retry: false },
  });
  if (!runId) return <Empty description={NOT_RUN_MESSAGE} />;
  const traces = tracesOf(result.data);
  return (
    <Flex vertical gap={12}>
      <Select
        aria-label="Run"
        value={runId}
        onChange={setRunId}
        style={{ maxWidth: 360 }}
        options={runs.map((r, i) => ({
          value: r.id,
          label: `${i === 0 ? "Latest" : `Run ${runs.length - i}`} · ${r.state} · ${r.passed}/${r.total}`,
        }))}
      />
      {query.isLoading ? (
        <Spin />
      ) : query.isError ? (
        <ApiErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : traces.length === 0 ? (
        <Empty description={NO_TRACE_MESSAGE} />
      ) : (
        <Collapse
          items={traces.map((t) => ({
            key: t.case_id,
            label: (
              <Flex gap={8} align="center">
                <span>{t.case_id}</span>
                <Tag color={t.error ? "error" : "default"}>{t.terminal ?? "FAILED"}</Tag>
              </Flex>
            ),
            children: <CaseTrace trace={t} />,
          }))}
        />
      )}
    </Flex>
  );
}

type Editing = { kind: "new" } | { kind: "edit"; testCase: TestCaseOut };

function CaseEditor({
  editing,
  schema,
  saving,
  serverProblems,
  onSave,
  onCancel,
}: {
  editing: Editing;
  schema: Record<string, unknown> | null;
  saving: boolean;
  serverProblems: FieldProblem[];
  onSave: (values: { name: string; input: Record<string, unknown>; terminal: Terminal }) => void;
  onCancel: () => void;
}) {
  const existing = editing.kind === "edit" ? editing.testCase : null;
  const [name, setName] = useState(existing?.name ?? "");
  const [terminal, setTerminal] = useState<Terminal>(existing?.expected_terminal ?? "SUCCESS");
  const [text, setText] = useState(JSON.stringify(existing?.input ?? {}, null, 2));
  const problems = useMemo(() => inputProblems(text, schema), [text, schema]);
  const nameOk = name.trim().length >= 1 && name.trim().length <= 120;
  const shown = problems.length ? problems : serverProblems;
  return (
    <Drawer
      open
      width={560}
      onClose={onCancel}
      title={existing ? `Edit ${existing.name}` : "Add a test case"}
      extra={
        <Button
          type="primary"
          loading={saving}
          disabled={problems.length > 0 || !nameOk}
          onClick={() =>
            onSave({
              name: name.trim(),
              input: JSON.parse(text) as Record<string, unknown>,
              terminal,
            })
          }
        >
          Save
        </Button>
      }
    >
      <Flex vertical gap={12}>
        {existing ? (
          <Meta>{existing.kind} case · the name and kind are fixed</Meta>
        ) : (
          <>
            <Meta>Your own case (kind FDE)</Meta>
            <Input
              aria-label="Name"
              placeholder="e.g. OTP wrong twice then right"
              maxLength={120}
              showCount
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </>
        )}
        <Select
          aria-label="Expected terminal state"
          value={terminal}
          onChange={setTerminal}
          options={TERMINALS.map((t) => ({ value: t, label: t }))}
        />
        <Input.TextArea
          aria-label="Input JSON"
          rows={12}
          spellCheck={false}
          style={{ fontFamily: "monospace" }}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {shown.length > 0 && (
          <ul aria-label="Input errors" style={{ margin: 0, paddingLeft: 18, color: "#b91c1c" }}>
            {shown.map((p) => (
              <li key={`${p.path} ${p.message}`}>
                <Typography.Text code>{p.path}</Typography.Text> {p.message}
              </li>
            ))}
          </ul>
        )}
      </Flex>
    </Drawer>
  );
}

export function TestPanel({
  projectId,
  workflowId,
  row,
  onClose,
  onRetry,
}: {
  projectId: string;
  workflowId: string;
  row: BuildRow;
  onClose: () => void;
  onRetry: (row: BuildRow) => void;
}) {
  const { message } = App.useApp();
  const subId = row.sub_skill_id;
  const tests = useCustom<TestsResponse, ApiError>({
    url: testsUrl(projectId, workflowId, subId),
    method: "get",
    queryOptions: { retry: false },
  });
  const runsQuery = useCustom<TestRunsResponse, ApiError>({
    url: runsUrl(projectId, workflowId, subId),
    method: "get",
    queryOptions: { retry: false },
  });
  const dataset = datasetOf(tests.result.data);
  const schema = inputSchemaOf(tests.result.data);
  const runs = runsOf(runsQuery.result.data);
  const latest = runs[0];
  const readOnly = isReadOnly(row.state);
  const [hidden, setHidden] = useState<Set<string>>(new Set()); // optimistic deletes
  const cases = casesOf(dataset).filter((c) => !hidden.has(c.id));
  const [stale, setStale] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [serverProblems, setServerProblems] = useState<FieldProblem[]>([]);
  const { mutateAsync, mutation } = useCustomMutation<BaseRecord, ApiError>();

  const failed = (e: unknown) => {
    const err = e as ApiError;
    if (err.statusCode === 422) {
      setServerProblems(fieldProblems(err));
      return;
    }
    const shown = writeError(err);
    if (shown.kind === "stale") setStale(true);
    else message.error(shown.text);
  };

  const write = (url: string, method: "put" | "post" | "delete", values: object) =>
    mutateAsync({
      url,
      method,
      values,
      config: { headers: { "if-match": `"${dataset?.sha256 ?? ""}"` } },
      successNotification: false,
      errorNotification: false,
    });

  const save = async (v: { name: string; input: Record<string, unknown>; terminal: Terminal }) => {
    if (!editing || !dataset) return;
    try {
      if (editing.kind === "edit")
        await write(caseUrl(projectId, workflowId, subId, editing.testCase.id), "put", {
          input: v.input,
          expected_terminal: v.terminal,
        });
      else
        await write(casesUrl(projectId, workflowId, subId), "post", {
          name: v.name,
          input: v.input,
          expected_terminal: v.terminal,
        });
      message.success(editing.kind === "edit" ? "Case saved" : `Case ${v.name} added`);
      setEditing(null);
      setServerProblems([]);
      await tests.query.refetch();
    } catch (e) {
      failed(e);
    }
  };

  const remove = async (c: TestCaseOut) => {
    setHidden((h) => new Set(h).add(c.id));
    try {
      await write(caseUrl(projectId, workflowId, subId, c.id), "delete", {});
      await tests.query.refetch();
    } catch (e) {
      failed(e);
    } finally {
      setHidden((h) => {
        const next = new Set(h);
        next.delete(c.id);
        return next; // gone after the refetch, or back after an error
      });
    }
  };

  const reload = async () => {
    setStale(false);
    await Promise.all([tests.query.refetch(), runsQuery.query.refetch()]);
  };

  let casesBody;
  if (tests.query.isLoading) casesBody = <Spin />;
  else if (tests.query.isError)
    casesBody = <ApiErrorState error={tests.query.error} onRetry={() => tests.query.refetch()} />;
  else if (!dataset)
    casesBody = <Empty description="No test data yet: it is generated with the build" />;
  else
    casesBody = (
      <Flex vertical gap={12}>
        <Flex justify="space-between" align="center">
          <Meta>
            Dataset v{dataset.version} · {cases.length} case{cases.length === 1 ? "" : "s"} ·
            synthetic
          </Meta>
          {!readOnly && (
            <Button
              onClick={() => {
                setServerProblems([]);
                setEditing({ kind: "new" });
              }}
            >
              Add case
            </Button>
          )}
        </Flex>
        <Table<TestCaseOut>
          size="small"
          pagination={false}
          rowKey="id"
          dataSource={cases}
          columns={[
            { title: "Name", dataIndex: "name" },
            { title: "Kind", dataIndex: "kind", width: 150, render: (k: string) => <Tag>{k}</Tag> },
            { title: "Expected", dataIndex: "expected_terminal", width: 110 },
            {
              title: "Last result",
              width: 110,
              render: (_, c) => {
                const r = RESULT[caseResult(latest, c.name)];
                return <Tag color={r.color}>{r.text}</Tag>;
              },
            },
            ...(readOnly
              ? []
              : [
                  {
                    title: "",
                    width: 150,
                    render: (_: unknown, c: TestCaseOut) => {
                      const deletable = canDelete(cases.length);
                      return (
                        <Flex gap={6}>
                          <Button
                            size="small"
                            aria-label={`Edit ${c.name}`}
                            onClick={() => {
                              setServerProblems([]);
                              setEditing({ kind: "edit", testCase: c });
                            }}
                          >
                            Edit
                          </Button>
                          {/* a native title on a wrapper: a disabled button fires no hover */}
                          <span title={deletable ? undefined : MIN_CASES_MESSAGE}>
                            <Button
                              size="small"
                              danger
                              disabled={!deletable || mutation.isPending}
                              aria-label={`Delete ${c.name}`}
                              onClick={() => remove(c)}
                            >
                              Delete
                            </Button>
                          </span>
                        </Flex>
                      );
                    },
                  },
                ]),
          ]}
        />
      </Flex>
    );

  return (
    <Drawer open width={960} onClose={onClose} title={`${row.skill_id} · Loop tests`}>
      <Flex vertical gap={12}>
        {stale && (
          <Alert
            type="warning"
            showIcon
            message={STALE_MESSAGE}
            action={
              <Button size="small" onClick={reload}>
                Reload
              </Button>
            }
          />
        )}
        {readOnly && <Alert type="info" showIcon message={FROZEN_MESSAGE} />}
        {row.state === "TESTING_BLOCKED" && (
          <Alert
            type="error"
            showIcon
            message="Testing is blocked: the sandbox gave no verdict"
            action={
              <Button size="small" onClick={() => onRetry(row)}>
                Retry
              </Button>
            }
          />
        )}
        {latest ? (
          <Flex gap={8} align="center">
            <Meta>Latest run</Meta>
            <Tag
              color={
                latest.state === "PASS" ? "success" : latest.state === "FAIL" ? "error" : "warning"
              }
            >
              {latest.state === "NON_CERTIFYING" ? "NON-CERTIFYING (local-docker)" : latest.state}
            </Tag>
            <Meta>
              {latest.passed}/{latest.total} cases passed
            </Meta>
          </Flex>
        ) : (
          !runsQuery.query.isLoading && <Meta>{NOT_RUN_MESSAGE}</Meta>
        )}
        <Tabs
          items={[
            { key: "cases", label: "Cases", children: casesBody },
            {
              key: "trace",
              label: "Trace",
              children: (
                <TraceTab projectId={projectId} workflowId={workflowId} subId={subId} runs={runs} />
              ),
            },
          ]}
        />
      </Flex>
      {editing && (
        <CaseEditor
          editing={editing}
          schema={schema}
          saving={mutation.isPending}
          serverProblems={serverProblems}
          onSave={save}
          onCancel={() => setEditing(null)}
        />
      )}
    </Drawer>
  );
}
