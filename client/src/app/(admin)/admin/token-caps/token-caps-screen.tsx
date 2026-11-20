"use client";

// Token caps per project (#776, ADR 0028 decision 6). Admin only. Pick a project; Save sends the five numbers
// with If-Match (the ETag of the read; "0" while the project has no limits of its own).

import { useCustomMutation, useList, useOne } from "@refinedev/core";
import { Alert, Button, Card, Flex, Form, InputNumber, Select, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { BudgetCaps, Project, ProjectTokenCaps } from "@/contracts";
import { ifMatch, type ApiError } from "@/providers/data-provider";

import {
  DEFAULTS_HELP,
  DEFAULTS_MESSAGE,
  FIELDS,
  ID,
  NOTE,
  STALE_MESSAGE,
  pathOf,
  resourceOf,
} from "./token-caps-api";

function CapsForm({ projectId }: { projectId: string }) {
  const [form] = Form.useForm<BudgetCaps>();
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [messages, holder] = message.useMessage();
  const resource = resourceOf(projectId);
  const { result, query } = useOne<ProjectTokenCaps, ApiError>({ resource, id: ID });
  const { mutateAsync, mutation } = useCustomMutation<ProjectTokenCaps, ApiError>();
  const view = result;

  // A reload (after a save or a stale write) puts the server's numbers in the form.
  useEffect(() => {
    if (view) form.setFieldsValue(view.caps);
  }, [view, form]);

  if (query.isLoading) return <LoadingState />;
  if (query.error && !view)
    return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!view) return <LoadingState />;

  const save = async (values: BudgetCaps) => {
    setSaveError(null);
    try {
      await mutateAsync({
        url: pathOf(projectId),
        method: "put",
        values,
        config: { headers: ifMatch(resource, ID) },
        successNotification: false,
        errorNotification: false,
      });
      await query.refetch(); // renews the ETag too
      messages.success("Saved");
    } catch (e) {
      setSaveError(e as ApiError);
    }
  };

  return (
    <Flex vertical gap={16}>
      {holder}
      {view.is_default && (
        <Alert type="info" showIcon message={DEFAULTS_MESSAGE} description={DEFAULTS_HELP} />
      )}
      {saveError?.statusCode === 412 && (
        <Alert
          type="info"
          showIcon
          message={STALE_MESSAGE}
          description="Reload to see the current numbers, then make your change again."
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
      {saveError && saveError.statusCode !== 412 && <ApiErrorState error={saveError} />}
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={view.caps}
        onFinish={(values) => void save(values)}
      >
        <Card size="small">
          {FIELDS.map((f) => (
            <Form.Item
              key={f.name}
              name={f.name}
              label={f.label}
              extra={f.hint}
              rules={[
                { required: true, message: `${f.label} is required` },
                {
                  type: "number",
                  min: f.min,
                  max: f.max,
                  message: `Use a whole number from ${f.min.toLocaleString("en-US")} to ${f.max.toLocaleString("en-US")}`,
                },
              ]}
            >
              <InputNumber
                style={{ width: "100%" }}
                precision={0}
                min={f.min}
                max={f.max}
                controls={false}
              />
            </Form.Item>
          ))}
          <Flex justify="flex-end">
            <Button type="primary" htmlType="submit" loading={mutation.isPending}>
              Save
            </Button>
          </Flex>
        </Card>
      </Form>
    </Flex>
  );
}

export function TokenCapsScreen() {
  const [picked, setPicked] = useState<string>();
  const { result, query } = useList<Project, ApiError>({
    resource: "admin/projects",
    pagination: { mode: "off" },
  });
  const projects = result.data ?? [];

  if (query.isLoading) return <LoadingState />;
  if (query.error) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;

  const projectId = picked ?? projects[0]?.id;
  return (
    <Flex vertical gap={16} style={{ maxWidth: 760 }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Token caps
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        {NOTE}
      </Typography.Paragraph>
      {projects.length === 0 || !projectId ? (
        <EmptyState message="No projects yet. Create a project first." />
      ) : (
        <>
          <Select
            aria-label="Project"
            value={projectId}
            onChange={setPicked}
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
            style={{ maxWidth: 420 }}
          />
          <CapsForm key={projectId} projectId={projectId} />
        </>
      )}
    </Flex>
  );
}
