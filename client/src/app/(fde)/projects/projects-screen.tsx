"use client";

// My projects (E4.10, TD §9.3): the projects assigned to this FDE as cards. An archived project drops out
// of the list on the server, so there is nothing to filter here.
import { useList } from "@refinedev/core";
import { Card, Col, Flex, Row, Typography } from "antd";
import Link from "next/link";

import { ApiErrorState, EmptyState, LoadingState, StatusBadge } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { EMPTY_PROJECTS, PROJECTS_RESOURCE } from "./workspace-api";

export function MyProjectsScreen() {
  const { result, query } = useList<Project, ApiError>({
    resource: PROJECTS_RESOURCE,
    pagination: { mode: "off" },
  });
  const projects = result.data ?? [];

  return (
    <Card>
      <Typography.Title level={1} style={{ marginTop: 0, fontSize: 24 }}>
        My projects
      </Typography.Title>
      {query.isLoading ? (
        <LoadingState />
      ) : query.error ? (
        <ApiErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : projects.length === 0 ? (
        <EmptyState message={EMPTY_PROJECTS} />
      ) : (
        <Row gutter={[16, 16]}>
          {projects.map((p) => (
            <Col key={p.id} xs={24} md={12} xl={8}>
              <Card
                size="small"
                hoverable
                title={
                  <Link href={`/projects/${p.id}`} aria-label={`Open ${p.name}`}>
                    {p.name}
                  </Link>
                }
                extra={<StatusBadge status={p.pipeline_state} />}
              >
                <Flex vertical gap={4}>
                  {p.client_name ? (
                    <Typography.Text type="secondary">{p.client_name}</Typography.Text>
                  ) : null}
                  {p.description ? (
                    <Typography.Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>
                      {p.description}
                    </Typography.Paragraph>
                  ) : null}
                </Flex>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </Card>
  );
}
