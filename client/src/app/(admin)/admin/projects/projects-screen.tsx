"use client";

// Layout follows the UI team's admin reference (docs/reference/ui/README.md, "Shared patterns"): title +
// description with actions on the right, tabs with count badges, search + "n of m rows", a bordered grid with
// small upper-case headers, and a footer line under the grid.

import { EditOutlined, InboxOutlined, PlusOutlined, TeamOutlined } from "@ant-design/icons";
import { useInvalidate, useList } from "@refinedev/core";
import {
  App,
  Button,
  ConfigProvider,
  Flex,
  Input,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useMemo, useState } from "react";

import {
  ApiErrorState,
  CountBadge,
  EmptyState,
  GRID_STYLE,
  GRID_THEME,
  head,
  LoadingState,
  Meta,
  MUTED,
  StatusBadge,
} from "@/components/ui-kit";
import type { Project, ProjectStatus } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { ArchiveDialog } from "./archive-dialog";
import { MembersDrawer } from "./members-drawer";
import { EMPTY_MESSAGE, RESOURCE } from "./project-api";
import { ProjectFormDrawer } from "./project-form-drawer";

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
    }),
    [projects],
  );
  const inTab = counts[tab];
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

  const grid = (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0" }}>
        <Input
          allowClear
          placeholder="Search projects..."
          aria-label="Search projects"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 340, maxWidth: "100%" }}
        />
        <Meta>
          {rows.length} of {inTab} rows
        </Meta>
      </Flex>
      <ConfigProvider theme={GRID_THEME}>
        <Table<Project>
          rowKey="id"
          bordered={false}
          dataSource={rows}
          pagination={rows.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          scroll={{ x: 820 }}
          style={GRID_STYLE}
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
              title: head("Project"),
              key: "name",
              render: (_, p) => (
                <Typography.Text ellipsis={{ tooltip: p.name }} style={{ maxWidth: 320 }}>
                  {p.name}
                </Typography.Text>
              ),
            },
            {
              title: head("Client"),
              key: "client",
              width: 220,
              render: (_, p) =>
                p.client_name ? (
                  <Typography.Text
                    type="secondary"
                    ellipsis={{ tooltip: p.client_name }}
                    style={{ maxWidth: 200 }}
                  >
                    {p.client_name}
                  </Typography.Text>
                ) : (
                  <Meta>—</Meta>
                ),
            },
            {
              title: head("Pipeline"),
              key: "pipeline",
              width: 180,
              render: (_, p) => <StatusBadge status={p.pipeline_state} />,
            },
            {
              title: head("Updated"),
              key: "updated",
              width: 160,
              render: (_, p) => (
                <Tooltip title={dayjs(p.updated_at).format("YYYY-MM-DD HH:mm:ss")}>
                  <Meta>{dayjs(p.updated_at).format("D MMM YYYY, HH:mm")}</Meta>
                </Tooltip>
              ),
            },
            {
              title: head("Actions"),
              key: "actions",
              width: 260,
              align: "right",
              render: (_, p) =>
                p.status === "ARCHIVED" ? (
                  <Tag icon={<InboxOutlined />}>Read-only</Tag>
                ) : (
                  <Flex gap={2} justify="flex-end">
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
      </ConfigProvider>
      <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
        <Meta>5 columns · newest first · archived projects are read-only</Meta>
        <Meta>Edit opens the project · FDEs assigns members · Archive asks for the name</Meta>
      </Flex>
      <div style={{ borderTop: "1px solid #e5e7eb", marginTop: 12, paddingTop: 10 }}>
        <Meta>
          <strong style={{ color: "#475569" }}>More fields →</strong> Description · Context notes
          (Markdown, with the masters template and document guide) — in Edit
        </Meta>
      </div>
    </>
  );

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (query.isError)
    body = <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (projects.length === 0) body = <EmptyState message={EMPTY_MESSAGE} action={newProject} />;
  else
    body = (
      <Tabs
        activeKey={tab}
        destroyOnHidden
        onChange={(key) => setTab(key as ProjectStatus)}
        items={(["ACTIVE", "ARCHIVED"] as const).map((key) => ({
          key,
          label: (
            <span>
              {key === "ACTIVE" ? "Active projects" : "Archived"}
              <CountBadge n={counts[key]} active={tab === key} />
            </span>
          ),
          children: grid,
        }))}
      />
    );

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap gap={12} style={{ marginBottom: 20 }}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            Projects
          </Typography.Title>
          <Typography.Text style={{ color: MUTED }}>
            Create tenant projects, add context notes and assign the FDEs who build them
          </Typography.Text>
        </div>
        {projects.length > 0 && newProject}
      </Flex>
      {body}

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
    </div>
  );
}
