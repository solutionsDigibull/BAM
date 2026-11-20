"use client";

import { ArrowLeftOutlined } from "@ant-design/icons";
import { useOne } from "@refinedev/core";
import Link from "next/link";

import { ApiErrorState, LoadingState } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { MastersScreen } from "./masters-screen";

/**
 * One project's masters page (#869). The project is read first: a cross-tenant or unknown id is the API's 404,
 * shown as not-found, and the masters calls then carry `project_id`. Masters storage stays tenant-level this round.
 */
export function ProjectMasters({ projectId }: { projectId: string }) {
  const { result, query } = useOne<Project, ApiError>({
    resource: "admin/projects",
    id: projectId,
  });
  if (query.isLoading) return <LoadingState />;
  if (query.isError) return <ApiErrorState error={query.error} onRetry={() => query.refetch()} />;
  return (
    <div>
      <Link href="/admin/masters" style={{ display: "inline-block", marginBottom: 12 }}>
        <ArrowLeftOutlined /> All projects
      </Link>
      <MastersScreen projectId={projectId} projectName={result?.name} />
    </div>
  );
}
