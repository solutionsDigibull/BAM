// What a project's pipeline state means to an FDE, and what the card's button says. Plain data and functions.
import type { ProjectPipelineState } from "@/contracts";

export const EMPTY_PROJECTS = "No projects assigned — ask your Admin.";

export type Tone = "idle" | "busy" | "review" | "done" | "failed";

const STATES: Record<ProjectPipelineState, { label: string; tone: Tone }> = {
  IDLE: { label: "Not started", tone: "idle" },
  INGESTING: { label: "Processing documents", tone: "busy" },
  KNOWLEDGE_BUILDING: { label: "Building knowledge", tone: "busy" },
  SOW_GENERATING: { label: "Generating the SOW", tone: "busy" },
  SOW_REVIEW: { label: "SOW ready for review", tone: "review" },
  SOW_APPROVED: { label: "SOW approved", tone: "done" },
  WORKFLOW_CREATED: { label: "Workflow created", tone: "done" },
  INGESTION_FAILED: { label: "Needs attention", tone: "failed" },
  KNOWLEDGE_FAILED: { label: "Needs attention", tone: "failed" },
  SOW_GENERATION_FAILED: { label: "Needs attention", tone: "failed" },
  CANCELLED: { label: "Cancelled", tone: "idle" },
};

export const stateOf = (state: ProjectPipelineState): { label: string; tone: Tone } =>
  STATES[state] ?? { label: state, tone: "idle" };

/** A project nobody has started opens on the goal screen; one in progress resumes where the work is. */
export const actionLabel = (state: ProjectPipelineState): string =>
  state === "IDLE" ? "Start" : "Resume";

/** The workbench resumes where the work is by itself: the link is the same for every state. */
export const workbenchHref = (projectId: string): string => `/projects/${projectId}/workbench`;
