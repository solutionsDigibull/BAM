"use client";

// Admin Masters home (#869): the tenant's projects as a repo-style list, with the tenant masters on top.

import { DatabaseOutlined, SearchOutlined } from "@ant-design/icons";
import { useList } from "@refinedev/core";
import { Card, Checkbox, Flex, Input, Tag, Typography } from "antd";
import dayjs from "dayjs";
import Link from "next/link";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState, Meta, MUTED } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export function MastersHome() {
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const { result, query } = useList<Project, ApiError>({
    resource: "admin/projects",
    pagination: { mode: "off" },
  });
  const projects = useMemo(() => result.data ?? [], [result.data]);
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return projects
      .filter((p) => archived || p.status !== "ARCHIVED")
      .filter(
        (p) =>
          !needle ||
          p.name.toLowerCase().includes(needle) ||
          (p.client_name ?? "").toLowerCase().includes(needle),
      )
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [projects, search, archived]);

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (query.isError) {
    body = <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  } else if (!projects.length) {
    body = (
      <EmptyState
        message="No projects yet. Create one under Admin, then manage its masters here."
        action={<Link href="/admin/projects">Go to Admin: Projects</Link>}
      />
    );
  } else if (!rows.length) {
    body = <EmptyState message="No projects match your search." />;
  } else {
    body = (
      <ul aria-label="Projects" className="repo-list">
        {rows.map((p) => (
          <li key={p.id} className="repo-row">
            <Flex justify="space-between" align="center" wrap gap={8}>
              <div style={{ minWidth: 0 }}>
                <Link
                  href={`/admin/masters/projects/${p.id}`}
                  style={{ fontWeight: 600, fontSize: 15 }}
                >
                  {p.name}
                </Link>
                <div>
                  <Meta>
                    {p.client_name ? `${p.client_name} · ` : ""}Updated{" "}
                    {dayjs(p.updated_at).format("D MMM YYYY, HH:mm")}
                  </Meta>
                </div>
              </div>
              <Tag color={p.status === "ARCHIVED" ? "default" : "green"}>
                {p.status === "ARCHIVED" ? "Archived" : "Active"}
              </Tag>
            </Flex>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div>
      <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
        Admin Masters
      </Typography.Title>
      <Typography.Text style={{ color: MUTED }}>
        Pick a project to manage its masters, or open the tenant masters
      </Typography.Text>
      <Link href="/admin/masters/tenant" style={{ display: "block", margin: "16px 0" }}>
        <Card size="small" hoverable>
          <Flex align="center" gap={12}>
            <DatabaseOutlined style={{ fontSize: 22 }} />
            <div>
              <div style={{ fontWeight: 600 }}>Tenant masters</div>
              <Meta>Organisations, rules, agents and skills shared by every project</Meta>
            </div>
          </Flex>
        </Card>
      </Link>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 12 }}>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="Search projects..."
          aria-label="Search projects"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 340, maxWidth: "100%" }}
        />
        <Checkbox checked={archived} onChange={(e) => setArchived(e.target.checked)}>
          Show archived
        </Checkbox>
      </Flex>
      {body}
    </div>
  );
}
