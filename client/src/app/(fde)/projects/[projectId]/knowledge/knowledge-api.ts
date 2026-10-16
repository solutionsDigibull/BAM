// Knowledge screens (E5.6 status panel, E5.5 explorer): API paths, rules and copy. Plain data and
// functions, so they are easy to test.
import type { KnowledgeVersionSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { problemCode } from "../../workspace-api";

export const knowledgePath = (projectId: string) => `projects/${projectId}/knowledge`;
export const rebuildPath = (projectId: string) => `projects/${projectId}/knowledge/rebuild`;
export const graphPath = (projectId: string, kvId: string) =>
  `projects/${projectId}/knowledge/${kvId}/graph`;
export const nodePath = (projectId: string, kvId: string, nodeId: string) =>
  `projects/${projectId}/knowledge/${kvId}/nodes/${encodeURIComponent(nodeId)}`;
/** The browser follows this one itself: the proxy forwards the API's 302 to the signed URL. */
export const okfDownloadUrl = (projectId: string, kvId: string) =>
  `/api/proxy/api/v1/projects/${projectId}/knowledge/${kvId}/okf-bundle`;
export const okfFileName = (projectName: string, version: number) =>
  `okf-${projectName.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "project"}-v${version}.zip`;

export const POLL_MS = 4000; // while a version is BUILDING (the issue asks for 3-5 s)
export const EMPTY_KNOWLEDGE = "No knowledge yet — start processing your documents.";
export const COVERAGE_UNAVAILABLE = "Coverage unavailable";
export const ALL_FAILED = "No document made it into the knowledge base. Rebuild after fixing them.";

export const isBuilding = (v: KnowledgeVersionSummary | undefined): boolean =>
  v?.status === "BUILDING";
export const canDownload = (v: KnowledgeVersionSummary | undefined): boolean =>
  v?.status === "READY" && Boolean(v.okf_sha256);

/** The newest version: the API sorts newest first, but never trust an order you can check. */
export const latest = (
  versions: KnowledgeVersionSummary[] | null | undefined,
): KnowledgeVersionSummary | undefined =>
  [...(versions ?? [])].sort((a, b) => b.version - a.version)[0];

const REBUILD_MESSAGES: Record<string, string> = {
  KNOWLEDGE_BUILDING: "Knowledge is already building.",
  PROJECT_ARCHIVED: "This project is archived and read-only.",
  TEMPORAL_UNAVAILABLE: "The processing engine is unavailable. Try again shortly.",
};

export function rebuildErrorMessage(error: unknown): string {
  const code = problemCode(error);
  const e = error as ApiError | null;
  return (
    (code && REBUILD_MESSAGES[code]) ?? e?.problem?.detail ?? e?.message ?? "Could not rebuild."
  );
}
