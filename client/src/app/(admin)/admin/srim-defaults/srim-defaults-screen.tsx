"use client";

// SRIM runtime defaults (E9.1b, O-19): the six runtime blocks every manifest of this tenant carries. Admin
// only. Save sends the whole set with If-Match; "Reset" returns the tenant to the ISA14 values.

import { useCustomMutation, useOne } from "@refinedev/core";
import { Alert, Button, Card, Flex, Form, Input, Popconfirm, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { ApiErrorState, LoadingState } from "@/components/ui-kit";
import type { SrimDefaultsView, SrimRuntimeDefaults } from "@/contracts";
import { ifMatch, type ApiError } from "@/providers/data-provider";

import {
  BLOCKS,
  DEFAULTS_HELP,
  DEFAULTS_MESSAGE,
  FIELDS,
  ID,
  MAX_LEN,
  NOTE,
  PATH,
  RESOURCE,
  STALE_MESSAGE,
  blockTitle,
} from "./srim-api";

export function SrimDefaultsScreen() {
  const [form] = Form.useForm<SrimRuntimeDefaults>();
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [messages, holder] = message.useMessage();
  const { result, query } = useOne<SrimDefaultsView, ApiError>({ resource: RESOURCE, id: ID });
  const { mutateAsync, mutation } = useCustomMutation<SrimDefaultsView, ApiError>();
  const view = result;

  // A reload (after a save, a reset or a stale write) puts the server's values in the form.
  useEffect(() => {
    if (view) form.setFieldsValue(view.defaults);
  }, [view, form]);

  if (query.isLoading) return <LoadingState />;
  if (query.error && !view)
    return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!view) return <LoadingState />;

  const send = async (method: "put" | "delete", values?: SrimRuntimeDefaults) => {
    setSaveError(null);
    try {
      await mutateAsync({
        url: PATH,
        method,
        values: values as object, // a DELETE has no body: the provider sends none for undefined
        config: { headers: ifMatch(RESOURCE, ID) },
        successNotification: false,
        errorNotification: false,
      });
      await query.refetch(); // renews the ETag too
      messages.success(method === "put" ? "Saved" : "Reset to the ISA14 defaults");
    } catch (e) {
      const error = e as ApiError;
      setSaveError(error);
      if (error.statusCode === 422 && error.errors)
        form.setFields(
          Object.entries(error.errors).map(([path, errors]) => ({
            name: path.split(".") as never, // the API's dotted path, e.g. ui.method
            errors: errors as string[],
          })),
        );
    }
  };

  return (
    <Flex vertical gap={16} style={{ maxWidth: 760 }}>
      {holder}
      <Typography.Title level={3} style={{ margin: 0 }}>
        SRIM runtime defaults
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {NOTE}
      </Typography.Paragraph>
      {view.is_default && (
        <Alert type="info" showIcon message={DEFAULTS_MESSAGE} description={DEFAULTS_HELP} />
      )}
      {saveError?.statusCode === 412 && (
        <Alert
          type="info"
          showIcon
          message={STALE_MESSAGE}
          description="Reload to see the current values, then make your change again."
          action={
            <Button
              size="small"
              type="primary"
              onClick={() => {
                setSaveError(null);
                void query.refetch();
              }}
            >
              Reload
            </Button>
          }
        />
      )}
      {saveError && ![412, 422].includes(saveError.statusCode) && (
        <ApiErrorState error={saveError} />
      )}
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={view.defaults}
        onFinish={(values) => void send("put", values)}
      >
        <Flex vertical gap={16}>
          {BLOCKS.map((block) => (
            <Card key={block} size="small" title={blockTitle(block)}>
              {FIELDS.filter((f) => f.block === block).map((f) => (
                <Form.Item
                  key={f.name}
                  name={[f.block, f.name]}
                  label={f.label}
                  extra={f.hint}
                  rules={[
                    { required: true, whitespace: true, message: `${f.label} is required` },
                    { max: MAX_LEN, message: `At most ${MAX_LEN} characters` },
                  ]}
                  normalize={(v: string) => v}
                >
                  <Input maxLength={MAX_LEN + 1} />
                </Form.Item>
              ))}
            </Card>
          ))}
          <Flex gap={8} justify="flex-end">
            <Popconfirm
              title="Reset to the ISA14 defaults?"
              description="Future manifests use the ISA14 values again. Built manifests are not changed."
              okText="Reset"
              onConfirm={() => void send("delete")}
              disabled={view.is_default}
            >
              <Button disabled={view.is_default || mutation.isPending}>Reset to ISA14</Button>
            </Popconfirm>
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              Save
            </Button>
          </Flex>
        </Flex>
      </Form>
    </Flex>
  );
}
