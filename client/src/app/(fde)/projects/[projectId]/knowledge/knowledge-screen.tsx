"use client";

// The Knowledge screen (TD §9 "Knowledge Explorer"): the status and coverage panel (E5.6); the read-only
// graph explorer (E5.5) renders below it for a READY or OUTDATED version.
import { useList } from "@refinedev/core";

import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { PROJECTS_RESOURCE } from "../../workspace-api";
import { KnowledgePanel } from "./knowledge-panel";

export function KnowledgeScreen({ projectId }: { projectId: string }) {
  const projects = useList<Project, ApiError>({
    resource: PROJECTS_RESOURCE,
    pagination: { mode: "off" },
  });
  const project = projects.result.data?.find((p) => p.id === projectId);
  return <KnowledgePanel projectId={projectId} projectName={project?.name ?? "project"} />;
}
