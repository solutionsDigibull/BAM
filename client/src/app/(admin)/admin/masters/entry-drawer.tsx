"use client";

import { useCustomMutation, useOne } from "@refinedev/core";
import { Alert, Button, Drawer, Flex, Form, Input, Select, Tag, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";

import { ApiErrorState, LoadingState, Meta } from "@/components/ui-kit";
import type { MasterEntry, MasterStatus, MasterTypeInfo } from "@/contracts";
import { ifMatch, type ApiError } from "@/providers/data-provider";

import {
  cellText,
  entriesResource,
  RAG_TEMPLATE,
  ragError,
  STATUS_COLOR,
  statusField,
  versionsUrl,
  withStatus,
} from "./masters-api";

const STATUSES: MasterStatus[] = ["PROPOSED", "APPROVED", "ILLUSTRATIVE", "RETIRED"];
const RAG = "rag_reference";

type Values = Record<string, string>;

const toValues = (info: MasterTypeInfo, entry?: MasterEntry): Values =>
  Object.fromEntries(
    info.columns.map((c) => {
      const v = entry?.payload[c.field];
      return [c.field, c.field === RAG && v ? JSON.stringify(v, null, 2) : cellText(v)];
    }),
  );

const toPayload = (values: Values) =>
  Object.fromEntries(
    Object.entries(values).map(([field, text]) => [
      field,
      field === RAG ? (text?.trim() ? JSON.parse(text) : null) : (text ?? ""),
    ]),
  );

const isStale = (e: ApiError) =>
  e.statusCode === 412 || (e.statusCode === 409 && e.problem?.code === "VERSION_CONFLICT");

/** Create (`entryId` unset) or version one master entry. Every save goes through the API. */
export function EntryDrawer({
  info,
  entryId,
  onClose,
  onSaved,
}: {
  info: MasterTypeInfo;
  entryId?: string;
  onClose: () => void;
  onSaved: (entry: MasterEntry) => void;
}) {
  const resource = entriesResource(info.master_type);
  const misc = statusField(info.master_type);
  const [form] = Form.useForm<Values>();
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const { result, query } = useOne<MasterEntry, ApiError>({
    resource,
    id: entryId ?? "",
    queryOptions: { enabled: !!entryId },
  });
  const entry = entryId ? result : undefined;
  const { mutateAsync, mutation } = useCustomMutation<MasterEntry, ApiError>();

  useEffect(() => {
    if (!entryId || entry) form.setFieldsValue(toValues(info, entry));
  }, [entry, entryId, form, info]);

  const miscText = Form.useWatch(misc, form) ?? "";
  const ragText = Form.useWatch(RAG, form) ?? "";
  const ragProblem = info.columns.some((c) => c.field === RAG) ? ragError(ragText) : null;
  const sample = entry?.source_kind === "BAM_0_2_SAMPLE";

  const save = async (values: Values) => {
    setError(null);
    try {
      const payload = toPayload(values);
      const res = await mutateAsync({
        url: entryId ? versionsUrl(info.master_type, entryId) : resource,
        method: "post",
        values: { payload },
        config: { headers: entryId ? ifMatch(resource, entryId) : {} },
        successNotification: false,
      });
      onSaved(res.data);
    } catch (e) {
      const err = e as ApiError;
      if (isStale(err)) return setStale(true); // never overwrite someone else's version silently
      if (err.statusCode === 422 && err.errors) {
        form.setFields(
          Object.entries(err.errors).map(([path, msgs]) => ({
            name: path.split(".")[1] ?? misc, // `payload.<field>`; a bare `payload` is the status
            errors: [msgs].flat().map(String),
          })),
        );
        return;
      }
      setError(err);
    }
  };

  const reload = async () => {
    const fresh = await query.refetch();
    form.setFieldsValue(toValues(info, fresh.data?.data));
    setStale(false);
  };

  return (
    <Drawer
      open
      width={640}
      onClose={onClose}
      title={entry ? `${info.tab} · ${entry.row_label}` : `New ${info.tab} entry`}
      extra={
        <Button
          type="primary"
          loading={mutation.isPending}
          disabled={!!ragProblem}
          onClick={() => form.submit()}
        >
          {entryId ? "Save new version" : "Create"}
        </Button>
      }
    >
      {entryId && query.isLoading ? (
        <LoadingState />
      ) : entryId && query.isError ? (
        <ApiErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : (
        <>
          {entry && (
            <Flex gap={8} align="center" style={{ marginBottom: 16 }}>
              <Tag>{entry.stable_id}</Tag>
              <Meta>version {entry.version}</Meta>
            </Flex>
          )}
          <Form.Item label="Status" style={{ marginBottom: 20 }}>
            <Tooltip title={sample ? "Sample rows (#1) are always Illustrative" : undefined}>
              <Select<MasterStatus>
                aria-label="Status"
                disabled={sample}
                style={{ width: 220 }}
                value={sample ? "ILLUSTRATIVE" : statusOf(miscText)}
                onChange={(s) => form.setFieldValue(misc, withStatus(miscText, s))}
                options={STATUSES.map((s) => ({
                  value: s,
                  label: <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
                }))}
              />
            </Tooltip>
            <Typography.Paragraph type="secondary" style={{ margin: "6px 0 0", fontSize: 12 }}>
              Saved as <code>Status: …</code> at the end of{" "}
              {misc === "misc" ? "Misc" : "Misc / Audit"}.
            </Typography.Paragraph>
          </Form.Item>
          <Form form={form} layout="vertical" onFinish={save} requiredMark={false}>
            {info.columns.map((c) => (
              <Form.Item
                key={c.field}
                name={c.field}
                label={c.header}
                tooltip={c.description || undefined}
                help={c.field === RAG && ragProblem ? ragProblem : undefined}
                validateStatus={c.field === RAG && ragProblem ? "error" : undefined}
                extra={
                  c.field === RAG ? (
                    <Button
                      size="small"
                      type="link"
                      style={{ padding: 0 }}
                      onClick={() => form.setFieldValue(RAG, RAG_TEMPLATE)}
                    >
                      Insert the 7-key template
                    </Button>
                  ) : undefined
                }
              >
                <Input.TextArea
                  aria-label={c.header}
                  autoSize={{ minRows: c.field === RAG ? 6 : 1, maxRows: 12 }}
                  style={c.field === RAG ? { fontFamily: "monospace" } : undefined}
                />
              </Form.Item>
            ))}
          </Form>
          {error && <ApiErrorState error={error} />}
          {stale && <ApiErrorState error={{ statusCode: 412 }} onReload={reload} />}
          {!entryId && (
            <Alert
              type="info"
              showIcon
              message="The row label (e.g. Rule #7) is assigned on save; the stable ID comes from the row."
            />
          )}
        </>
      )}
    </Drawer>
  );
}

// The status a Misc text means, as the server reads it (a trailing `Status: X`, else Proposed).
function statusOf(misc: string): MasterStatus {
  const m = /(?:^|[\s;])Status:\s*(\S+?)\.?\s*$/i.exec(misc.trim());
  const value = m?.[1]?.toUpperCase();
  return STATUSES.find((s) => s === value) ?? "PROPOSED";
}
