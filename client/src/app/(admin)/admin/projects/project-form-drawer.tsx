"use client";

import { useCreate, useOne, useUpdate } from "@refinedev/core";
import {
  Alert,
  Button,
  Collapse,
  Drawer,
  Flex,
  Form,
  Input,
  List,
  Segmented,
  Space,
  Tag,
  Typography,
} from "antd";
import { useState } from "react";

import { ApiErrorState, LoadingState } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { MarkdownPreview } from "./markdown-preview";
import { CONTEXT_NOTES_TEMPLATE, INTAKE_RULES, MASTER_GUIDE } from "./master-guide";
import {
  ARCHIVED_MESSAGE,
  DUPLICATE_NAME_MESSAGE,
  LIMITS,
  RESOURCE,
  problemCode,
  statusOf,
  toBody,
  toForm,
  type ProjectForm,
} from "./project-api";

const NO_CONTROL = /^[^\x00-\x1f\x7f-\x9f]*$/; // same rule as the contract
const FIELDS = new Set(["name", "client_name", "description", "context_notes"]);

/** README §7 of the admin masters workbook: what the FDE should bring for each master. */
function MasterGuidePanel() {
  return (
    <>
      <List
        size="small"
        dataSource={MASTER_GUIDE}
        renderItem={(g) => (
          <List.Item>
            <List.Item.Meta
              title={
                <Space size={8} wrap>
                  <Typography.Text strong>{g.master}</Typography.Text>
                  <Tag>{g.intent}</Tag>
                </Space>
              }
              description={
                <>
                  <div>{g.docs}</div>
                  <Typography.Text type="secondary">{g.route}</Typography.Text>
                </>
              }
            />
          </List.Item>
        )}
      />
      <ul style={{ margin: "8px 0 0", paddingLeft: 20, color: "rgba(0,0,0,0.55)" }}>
        {INTAKE_RULES.map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
    </>
  );
}

/** Create (no `projectId`) or edit a project. Edits read the project first, so PATCH carries its ETag. */
export function ProjectFormDrawer({
  projectId,
  onClose,
  onSaved,
}: {
  projectId?: string;
  onClose: () => void;
  onSaved: (project: Project) => void;
}) {
  const editing = projectId !== undefined;
  const [form] = Form.useForm<ProjectForm>();
  const notes = Form.useWatch("context_notes", form);
  const [notesTab, setNotesTab] = useState<"Write" | "Preview">("Write");
  const [error, setError] = useState<ApiError | null>(null);
  const [stale, setStale] = useState(false);

  const { result: current, query } = useOne<Project, ApiError>({
    resource: RESOURCE,
    id: projectId ?? "",
    queryOptions: { enabled: editing },
  });
  const create = useCreate<Project, ApiError, ProjectForm>();
  const update = useUpdate<Project, ApiError, ProjectForm>();
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    const fresh = await query.refetch();
    if (fresh.data?.data) {
      form.resetFields();
      form.setFieldsValue(toForm(fresh.data.data));
    }
    setStale(false);
    setError(null);
  };

  const fail = (e: ApiError) => {
    if (statusOf(e) === 412) return setStale(true); // never overwrite the admin's edits silently
    if (problemCode(e) === "DUPLICATE_NAME") {
      return form.setFields([{ name: "name", errors: [DUPLICATE_NAME_MESSAGE] }]);
    }
    const onFields = Object.entries(e.errors ?? {}).filter(([path]) => FIELDS.has(path));
    if (statusOf(e) === 422 && onFields.length) {
      return form.setFields(
        onFields.map(([path, msgs]) => ({
          name: path as keyof ProjectForm,
          errors: [msgs].flat().map(String),
        })),
      );
    }
    setError(e);
  };

  const submit = async (values: ProjectForm) => {
    setError(null);
    setSaving(true);
    const body = toBody(values);
    try {
      const saved = editing
        ? await update.mutateAsync({ resource: RESOURCE, id: projectId, values: body })
        : await create.mutateAsync({ resource: RESOURCE, values: body });
      onSaved(saved.data);
    } catch (e) {
      fail(e as ApiError);
    } finally {
      setSaving(false);
    }
  };

  const archived = current?.status === "ARCHIVED";
  const loading = editing && query.isLoading;
  // The form mounts once the project is read and takes it as initial values. A later refetch (focus,
  // 412) never replaces the admin's edits by itself: the latest version lands only on Reload.
  const showForm = !editing || current !== undefined;

  return (
    <Drawer
      open
      width={640}
      title={editing ? "Edit project" : "New project"}
      onClose={onClose}
      destroyOnHidden
      extra={
        <Space>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            type="primary"
            loading={saving}
            disabled={loading || archived || query.isError}
            onClick={() => form.submit()}
          >
            {editing ? "Save changes" : "Create project"}
          </Button>
        </Space>
      }
    >
      {stale && <ApiErrorState error={{ statusCode: 412 }} onReload={reload} />}
      {loading && <LoadingState />}
      {editing && query.isError && (
        <ApiErrorState error={query.error} onRetry={() => query.refetch()} />
      )}
      {archived && (
        <Alert type="info" showIcon message={ARCHIVED_MESSAGE} style={{ marginBottom: 16 }} />
      )}
      {error && (
        <div style={{ marginBottom: 16 }}>
          {problemCode(error) === "PROJECT_ARCHIVED" ? (
            <Alert type="info" showIcon message={ARCHIVED_MESSAGE} />
          ) : (
            <ApiErrorState error={error} onRetry={() => form.submit()} />
          )}
        </div>
      )}
      {showForm && (
        <Form<ProjectForm>
          form={form}
          layout="vertical"
          requiredMark="optional"
          onFinish={submit}
          disabled={archived || saving}
          initialValues={current ? toForm(current) : undefined}
        >
          <Form.Item
            name="name"
            label="Project name"
            rules={[
              { required: true, whitespace: true, message: "Name is required." },
              { max: LIMITS.name, message: `At most ${LIMITS.name} characters.` },
              { pattern: NO_CONTROL, message: "Remove control characters." },
            ]}
          >
            <Input
              placeholder="e.g. Plant 4 quality automation"
              maxLength={LIMITS.name}
              showCount
            />
          </Form.Item>
          <Form.Item
            name="client_name"
            label="Client"
            rules={[
              { max: LIMITS.client_name, message: `At most ${LIMITS.client_name} characters.` },
              { pattern: NO_CONTROL, message: "Remove control characters." },
            ]}
          >
            <Input
              placeholder="Customer or business unit"
              maxLength={LIMITS.client_name}
              showCount
            />
          </Form.Item>
          <Form.Item
            name="description"
            label="Description"
            rules={[
              { max: LIMITS.description, message: `At most ${LIMITS.description} characters.` },
            ]}
          >
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 6 }}
              maxLength={LIMITS.description}
              showCount
            />
          </Form.Item>
          <Flex justify="space-between" align="center" style={{ marginBottom: 8 }}>
            <Typography.Text strong>Context notes</Typography.Text>
            <Segmented<"Write" | "Preview">
              size="small"
              options={["Write", "Preview"]}
              value={notesTab}
              onChange={setNotesTab}
            />
          </Flex>
          <Flex justify="space-between" align="baseline" gap={8} style={{ marginBottom: 8 }}>
            <Typography.Text type="secondary">
              Markdown. SBAM gives these notes to every stage as project context.
            </Typography.Text>
            {!notes?.trim() && !archived && (
              <Button
                size="small"
                type="link"
                onClick={() => {
                  form.setFieldValue("context_notes", CONTEXT_NOTES_TEMPLATE);
                  setNotesTab("Write");
                }}
              >
                Insert template
              </Button>
            )}
          </Flex>
          <Form.Item
            name="context_notes"
            hidden={notesTab === "Preview"}
            rules={[
              { max: LIMITS.context_notes, message: `At most ${LIMITS.context_notes} characters.` },
            ]}
          >
            <Input.TextArea
              aria-label="Context notes"
              autoSize={{ minRows: 6, maxRows: 16 }}
              maxLength={LIMITS.context_notes}
              showCount
            />
          </Form.Item>
          {notesTab === "Preview" && (
            <div
              style={{
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                padding: "8px 16px",
                minHeight: 140,
                background: "#fafafa",
              }}
            >
              <MarkdownPreview source={notes} />
            </div>
          )}
          <Collapse
            size="small"
            style={{ marginTop: 16 }}
            items={[
              {
                key: "guide",
                label: "Which documents feed which master",
                children: <MasterGuidePanel />,
              },
            ]}
          />
        </Form>
      )}
    </Drawer>
  );
}
