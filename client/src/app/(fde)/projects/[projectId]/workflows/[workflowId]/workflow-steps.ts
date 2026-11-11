// A Workflow's life as the FDE sees it, and what to do next in each state. Plain data, easy to test.
import type { WorkflowState } from "@/contracts";

export const LIFECYCLE = [
  { key: "review", label: "ISATVON review", path: "isatvon" },
  { key: "build", label: "Build & test", path: "build" },
  { key: "certify", label: "Certification", path: "certification" },
  { key: "frozen", label: "Frozen", path: "versions" },
] as const;

/** Index into LIFECYCLE of the step the state is at (-1 = before it, e.g. still extracting). */
export function stepIndex(state: WorkflowState): number {
  switch (state) {
    case "ISATVON_REVIEW":
    case "NEEDS_FDE_REVIEW":
      return 0;
    case "BUILDING":
    case "WORKFLOW_VALIDATION_FAILED":
      return 1;
    case "CERTIFYING":
      return 2;
    case "CERTIFIED":
      return 2;
    case "FROZEN":
      return 3;
    default:
      return -1;
  }
}

export const isFailure = (state: WorkflowState): boolean =>
  state === "EXTRACTION_FAILED" || state === "WORKFLOW_VALIDATION_FAILED";

type Next = { label: string; path: string; hint: string };

const NEXT: Partial<Record<WorkflowState, Next>> = {
  ISATVON_EXTRACTING: {
    label: "Watch the breakdown",
    path: "isatvon",
    hint: "SBAM is breaking the SOW into modules and skills.",
  },
  ISATVON_REVIEW: {
    label: "Review the breakdown",
    path: "isatvon",
    hint: "Check the agents and skills, then generate to start the build.",
  },
  NEEDS_FDE_REVIEW: {
    label: "Fix what needs review",
    path: "isatvon",
    hint: "Something in the breakdown needs your decision before the build.",
  },
  EXTRACTION_FAILED: {
    label: "See why it failed",
    path: "isatvon",
    hint: "The breakdown could not be produced. Open it to retry.",
  },
  BUILDING: {
    label: "Open build & test",
    path: "build",
    hint: "Code and tests are being generated and run in the sandbox.",
  },
  WORKFLOW_VALIDATION_FAILED: {
    label: "Open build & test",
    path: "build",
    hint: "The generated workflow did not validate. Open the build to see the findings.",
  },
  CERTIFYING: {
    label: "Open certification",
    path: "certification",
    hint: "The sandbox certification run is in progress.",
  },
  CERTIFIED: {
    label: "Open certification",
    path: "certification",
    hint: "Certified. Review the result and freeze the version.",
  },
  FROZEN: { label: "See versions", path: "versions", hint: "Frozen: this version is final." },
};

export const nextStep = (state: WorkflowState): Next | undefined => NEXT[state];
