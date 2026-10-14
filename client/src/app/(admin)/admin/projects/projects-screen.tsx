"use client";

import {
  EditOutlined,
  InboxOutlined,
  PlusOutlined,
  SearchOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useInvalidate, useList } from "@refinedev/core";
import {
  App,
  Button,
  Card,
  Col,
  Flex,
  Input,
  Row,
  Segmented,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { Project, ProjectStatus } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { ArchiveDialog } from "./archive-dialog";
import { MembersDrawer } from "./members-drawer";
import { EMPTY_MESSAGE, RESOURCE } from "./project-api";
import { ProjectFormDrawer } from "./project-form-drawer";

// Pipeline states in which SBAM is working on the project (TD §14), counted as "processing".
const BUSY = new Set(["INGESTING", "KNOWLEDGE_BUILDING", "SOW_GENERATING", "SOW_APPROVED"]);

type Editor = { mode: "create" } | { mode: "edit"; id: string };

export function ProjectsScreen() {
  const { message } = App.useApp();
  const invalidate = useInvalidate();
  const [tab, setTab] = useState<ProjectStatus>("ACTIVE");
  const [search, setSearch] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [archiving, setArchiving] = useState<Project | null>(null);
  const [members, setMembers] = useState<Project | null>(null);

  const { result, query } = useList<Project, ApiError>({
    resource: RESOURCE,
    pagination: { mode: "off" },
  });
  const projects = useMemo(() => result.data ?? [], [result.data]);
  const refresh = () => invalidate({ resource: RESOURCE, invalidates: ["list", "detail"] });

  const counts = useMemo(
    () => ({
      ACTIVE: projects.filter((p) => p.status === "ACTIVE").length,
      ARCHIVED: projects.filter((p) => p.status === "ARCHIVED").length,
      busy: projects.filter((p) => p.status === "ACTIVE" && BUSY.has(p.pipeline_state)).length,
    }),
    [projects],
  );
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return projects
      .filter((p) => p.status === tab)
      .filter(
        (p) =>
          !needle ||
          p.name.toLowerCase().includes(needle) ||
          (p.client_name ?? "").toLowerCase().includes(needle),
      )
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [projects, tab, search]);

  const newProject = (
    <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditor({ mode: "create" })}>
      New project
    </Button>
  );

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (query.isError)
    body = <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (projects.length === 0) body = <EmptyState message={EMPTY_MESSAGE} action={newProject} />;
  else
    body = (
      <>
        <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 16 }}>
          <Segmented<ProjectStatus>
            value={tab}
            onChange={setTab}
            options={[
              { value: "ACTIVE", label: `Active (${counts.ACTIVE})` },
              { value: "ARCHIVED", label: `Archived (${counts.ARCHIVED})` },
            ]}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="Search name or client"
            aria-label="Search projects"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 280, maxWidth: "100%" }}
          />
        </Flex>
        <Table<Project>
          rowKey="id"
          dataSource={rows}
          pagination={rows.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          scroll={{ x: 760 }}
          locale={{
            emptyText: (
              <EmptyState
                message={
                  search
                    ? "No projects match your search."
                    : tab === "ARCHIVED"
                      ? "No archived projects."
                      : "No active projects."
                }
              />
            ),
          }}
          columns={[
            {
              title: "Project",
              key: "name",
              render: (_, p) => (
                <Flex vertical style={{ minWidth: 0 }}>
                  <Typography.Text strong ellipsis={{ tooltip: p.name }} style={{ maxWidth: 360 }}>
                    {p.name}
                  </Typography.Text>
                  {p.client_name && (
                    <Typography.Text
                      type="secondary"
                      ellipsis={{ tooltip: p.client_name }}
                      style={{ maxWidth: 360, fontSize: 12 }}
                    >
                      {p.client_name}
                    </Typography.Text>
                  )}
                </Flex>
              ),
            },
            {
              title: "Pipeline",
              key: "pipeline",
              width: 190,
              render: (_, p) => <StatusBadge status={p.pipeline_state} />,
            },
            {
              title: "Updated",
              key: "updated",
              width: 170,
              render: (_, p) => (
                <Tooltip title={dayjs(p.updated_at).format("YYYY-MM-DD HH:mm:ss")}>
                  <Typography.Text type="secondary">
                    {dayjs(p.updated_at).format("D MMM YYYY, HH:mm")}
                  </Typography.Text>
                </Tooltip>
              ),
            },
            {
              title: "Actions",
              key: "actions",
              width: 300,
              align: "right",
              render: (_, p) =>
                p.status === "ARCHIVED" ? (
                  <Tag icon={<InboxOutlined />}>Read-only</Tag>
                ) : (
                  <Flex gap={4} justify="flex-end">
                    <Button
                      size="small"
                      type="text"
                      icon={<EditOutlined />}
                      aria-label={`Edit ${p.name}`}
                      onClick={() => setEditor({ mode: "edit", id: p.id })}
                    >
                      Edit
                    </Button>
                    <Button
                      size="small"
                      type="text"
                      icon={<TeamOutlined />}
                      aria-label={`FDEs of ${p.name}`}
                      onClick={() => setMembers(p)}
                    >
                      FDEs
                    </Button>
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<InboxOutlined />}
                      aria-label={`Archive ${p.name}`}
                      onClick={() => setArchiving(p)}
                    >
                      Archive
                    </Button>
                  </Flex>
                ),
            },
          ]}
        />
      </>
    );

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="flex-start" wrap gap={12}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 24 }}>
            Projects
          </Typography.Title>
          <Typography.Text type="secondary">
            Create projects, add context notes and assign the FDEs who will build them.
          </Typography.Text>
        </div>
        {projects.length > 0 && newProject}
      </Flex>
      {projects.length > 0 && (
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic title="Active projects" value={counts.ACTIVE} />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic title="Processing now" value={counts.busy} />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic title="Archived" value={counts.ARCHIVED} />
            </Card>
          </Col>
        </Row>
      )}
      <Card>{body}</Card>

      {editor && (
        <ProjectFormDrawer
          projectId={editor.mode === "edit" ? editor.id : undefined}
          onClose={() => setEditor(null)}
          onSaved={(p) => {
            setEditor(null);
            void refresh();
            message.success(editor.mode === "create" ? `Created ${p.name}` : `Saved ${p.name}`);
            if (editor.mode === "create") setMembers(p); // next step: assign FDEs
          }}
        />
      )}
      {archiving && (
        <ArchiveDialog
          project={archiving}
          onClose={() => setArchiving(null)}
          onArchived={(p) => {
            setArchiving(null);
            void refresh();
            message.success(`Archived ${p.name}`);
          }}
        />
      )}
      {members && (
        <MembersDrawer
          project={members}
          onClose={() => setMembers(null)}
          onSaved={() => {
            setMembers(null);
            message.success("FDE assignment saved");
          }}
        />
      )}
    </Flex>
  );
}
