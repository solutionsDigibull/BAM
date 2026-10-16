// Shared bits of the FDE workspace screens (E4.10): API paths, the rules the buttons follow, the problem
// codes they branch on and the copy. Everything here is plain data and functions so it is easy to test.
import type { PipelineCounts, PipelineTimeline, ProjectPipelineState } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const PROJECTS_RESOURCE = "projects";
export const documentsResource = (projectId: string) => `projects/${projectId}/documents`;
export const pipelinePath = (projectId: string) => `projects/${projectId}/pipeline`;
export const startPath = (projectId: string) => `projects/${projectId}/pipeline/start`;
export const workflowsPath = (projectId: string) => `projects/${projectId}/workflows`;
export const uploadsPath = (projectId: string) => `projects/${projectId}/uploads`;
export const retryPath = (projectId: string, documentId: string) =>
  `projects/${projectId}/documents/${documentId}/retry`;
export const restorePath = (projectId: string, documentId: string) =>
  `projects/${projectId}/documents/${documentId}/restore`;

export const EMPTY_PROJECTS = "No projects assigned — ask your Admin.";
export const EMPTY_OVERVIEW = "Upload documents to start";
export const EMPTY_WORKFLOWS = "Workflows appear here after the SOW is approved.";
export const EMPTY_DOCUMENTS = "No documents uploaded yet.";

export const problemCode = (error: unknown): string | undefined =>
  (error as ApiError | null)?.problem?.code;

// --- Start processing -------------------------------------------------------------------------------

/** The states the API starts processing from: a fresh project, or a retry after a failed ingestion. */
export const START_STATES: readonly ProjectPipelineState[] = ["IDLE", "INGESTION_FAILED"];
/** States where a run is in progress, so the overview keeps refreshing. */
export const RUNNING_STATES: readonly ProjectPipelineState[] = [
  "INGESTING",
  "KNOWLEDGE_BUILDING",
  "SOW_GENERATING",
];

/** Documents that count: uploaded or being scanned, parsed, or failed (rejected ones do not). */
export const activeDocuments = (c: PipelineCounts): number => c.uploaded + c.parsed + c.failed;

/** The button is only a convenience: the API decides (409 `PIPELINE_RUNNING`, `NO_DOCUMENTS`, ...). */
export const canStart = (t: PipelineTimeline): boolean =>
  START_STATES.includes(t.state) && activeDocuments(t.counts) >= 1;

export const isRunning = (state: ProjectPipelineState): boolean => RUNNING_STATES.includes(state);

const START_MESSAGES: Record<string, string> = {
  PIPELINE_RUNNING: "Processing is already running.",
  NO_DOCUMENTS: "Upload at least one document before starting.",
  INVALID_TRANSITION: "Processing cannot be started in the project's current state.",
  PROJECT_ARCHIVED: "This project is archived and read-only.",
  ORCHESTRATOR_UNAVAILABLE: "The processing engine is unavailable. Try again shortly.",
};

export function startErrorMessage(error: unknown): string {
  const code = problemCode(error);
  const e = error as ApiError | null;
  return (code && START_MESSAGES[code]) ?? e?.problem?.detail ?? e?.message ?? "Could not start.";
}

// --- Timeline ---------------------------------------------------------------------------------------

/** Index into `PIPELINE_STEPS` (Upload, Knowledge, SOW, Approve, ISATVON, Build, Certified, Frozen). The
 *  API's project states stop at WORKFLOW_CREATED; the later steps come with the Workflow states. */
const STEP_OF: Record<ProjectPipelineState, number> = {
  IDLE: 0,
  INGESTING: 0,
  INGESTION_FAILED: 0,
  KNOWLEDGE_BUILDING: 1,
  KNOWLEDGE_FAILED: 1,
  SOW_GENERATING: 2,
  SOW_GENERATION_FAILED: 2,
  SOW_REVIEW: 3, // waiting for the FDE to approve
  SOW_APPROVED: 4,
  WORKFLOW_CREATED: 4,
  CANCELLED: 0,
};

export function timelineView(t: PipelineTimeline): { current: number; failed: boolean } {
  // A cancelled run stopped wherever it was: the API marks that step.
  const cancelled = t.steps.find((s) => s.status === "cancelled");
  return {
    current: STEP_OF[cancelled?.name ?? t.state] ?? 0,
    failed: t.state.endsWith("_FAILED"),
  };
}
