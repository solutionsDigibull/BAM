"use client";

import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { useCustomMutation, useOne } from "@refinedev/core";
import { Button, Drawer, Flex, Form, Input, Select, Tabs, Tag } from "antd";
import { useEffect, useState } from "react";

import { ApiErrorState, LoadingState, Meta } from "@/components/ui-kit";
import type { MasterEntry, MasterTypeInfo, MicroPrompt } from "@/contracts";
import { ifMatch, type ApiError } from "@/providers/data-provider";

import { cellText, entriesResource, versionsUrl } from "./masters-api";
import { cleanPrompts, MAX_MICRO, promptsOf, RULE_STATUSES } from "./rules-tools";

/** Editor fields of the Rule tab that the layout has no column for (RulesPayloadV03). */
const EXTRA = [
  ["applies_to", "Applies to"],
  ["fde_use", "FDE usage"],
  ["client_use", "Client representative usage"],
  ["acts_on", "Acts on"],
] as const;

type Values = { system: string; micro: MicroPrompt[]; [field: string]: unknown };

const isStale = (e: ApiError) =>
  e.statusCode === 412 || (e.statusCode === 409 && e.problem?.code === "VERSION_CONFLICT");

/** The Rules Engine editor (#784): a Rule tab and a Prompts tab. A save is a new version (If-Match). */
export function RuleDrawer({
  info,
  entryId,
  projectId,
  onClose,
  onSaved,
}: {
  info: MasterTypeInfo;
  entryId?: string;
  projectId?: string;
  onClose: () => void;
  onSaved: (entry: MasterEntry) => void;
}) {
  const resource = entriesResource("RULES");
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
  const fields = [...info.columns.map((c) => c.field), ...EXTRA.map(([f]) => f)];

  const load = (e?: MasterEntry) =>
    form.setFieldsValue({
      ...Object.fromEntries(fields.map((f) => [f, cellText(e?.payload[f])])),
      ...promptsOf(e),
    });
  useEffect(() => {
    if (!entryId || entry) load(entry);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry, entryId]);

  const save = async ({ system, micro, ...rest }: Values) => {
    setError(null);
    try {
      const res = await mutateAsync({
        url: entryId
          ? versionsUrl("RULES", entryId)
          : `${resource}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
        method: "post",
        values: { payload: { ...entry?.payload, ...rest, prompts: cleanPrompts(system, micro) } },
        config: { headers: entryId ? ifMatch(resource, entryId) : {} },
        successNotification: false,
      });
      onSaved(res.data);
    } catch (e) {
      const err = e as ApiError;
      if (isStale(err)) return setStale(true); // never overwrite someone else's version silently
      setError(err);
    }
  };

  const ruleTab = (
    <>
      {info.columns.map((c) => (
        <Form.Item key={c.field} name={c.field} label={c.header} tooltip={c.description}>
          {c.field === "record_status" ? (
            <Select aria-label={c.header} options={RULE_STATUSES.map((s) => ({ value: s }))} />
          ) : (
            <Input.TextArea aria-label={c.header} autoSize={{ minRows: 1, maxRows: 10 }} />
          )}
        </Form.Item>
      ))}
      {EXTRA.map(([field, label]) => (
        <Form.Item key={field} name={field} label={label}>
          <Input.TextArea aria-label={label} autoSize={{ minRows: 1, maxRows: 10 }} />
        </Form.Item>
      ))}
    </>
  );
  const promptsTab = (
    <>
      <Form.Item name="system" label="System prompt">
        <Input.TextArea aria-label="System prompt" autoSize={{ minRows: 3, maxRows: 12 }} />
      </Form.Item>
      <Form.List name="micro">
        {(items, { add, remove }) => (
          <>
            {items.map((item, i) => (
              <Flex key={item.key} gap={8} align="start" wrap>
                <Form.Item name={[item.name, "name"]} style={{ flex: "1 1 140px" }}>
                  <Input aria-label={`Micro prompt ${i + 1} name`} placeholder="Name" />
                </Form.Item>
                <Form.Item name={[item.name, "text"]} style={{ flex: "3 1 220px" }}>
                  <Input.TextArea
                    aria-label={`Micro prompt ${i + 1} text`}
                    placeholder="Prompt text"
                    autoSize={{ minRows: 1, maxRows: 8 }}
                  />
                </Form.Item>
                <Button
                  type="text"
                  danger
                  icon={<MinusCircleOutlined />}
                  aria-label={`Remove micro prompt ${i + 1}`}
                  onClick={() => remove(item.name)}
                />
              </Flex>
            ))}
            <Button
              icon={<PlusOutlined />}
              disabled={items.length >= MAX_MICRO}
              onClick={() => add({ name: "", text: "" })}
            >
              Add micro prompt
            </Button>
          </>
        )}
      </Form.List>
    </>
  );

  return (
    <Drawer
      open
      width={640}
      onClose={onClose}
      title={entry ? `Rule · ${entry.stable_id}` : "New rule"}
      extra={
        <Button type="primary" loading={mutation.isPending} onClick={() => form.submit()}>
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
            <Flex gap={8} align="center" style={{ marginBottom: 12 }}>
              <Tag>{entry.stable_id}</Tag>
              <Meta>version {entry.version}</Meta>
            </Flex>
          )}
          <Form form={form} layout="vertical" onFinish={save} requiredMark={false}>
            <Tabs
              items={[
                { key: "rule", label: "Rule", children: ruleTab },
                { key: "prompts", label: "Prompts", forceRender: true, children: promptsTab },
              ]}
            />
          </Form>
          {error && <ApiErrorState error={error} />}
          {stale && (
            <ApiErrorState
              error={{ statusCode: 412 }}
              onReload={async () => {
                const fresh = await query.refetch();
                load(fresh.data?.data);
                setStale(false);
              }}
            />
          )}
        </>
      )}
    </Drawer>
  );
}
