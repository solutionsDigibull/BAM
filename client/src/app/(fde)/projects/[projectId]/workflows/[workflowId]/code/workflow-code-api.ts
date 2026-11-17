// The whole-Workflow code preview's data rules (ADR 0028, WG-15), free of React so they can be tested
// alone. Read only: this module only builds URLs and summarises what the API returned.
import type { FileTag, WorkflowCodeFile, WorkflowCodeGroup } from "@/contracts";

export const workflowCodeUrl = (projectId: string, workflowId: string) =>
  `projects/${projectId}/workflows/${workflowId}/code`;
export const workflowFileUrl = (projectId: string, workflowId: string) =>
  `${workflowCodeUrl(projectId, workflowId)}/files`;

/** The per-attempt viewer (attempts, files, diffs) of one Sub-Skill, or null for the Workflow group. */
export const attemptsLink = (projectId: string, workflowId: string, group: WorkflowCodeGroup) =>
  group.kind === "sub_skill"
    ? `/projects/${projectId}/workflows/${workflowId}/code?sub=${group.group_id}`
    : null;

export const TAG_LABEL: Record<FileTag, string> = {
  reused: "Reused",
  customized: "Customized",
  generated: "Generated",
};
export const TAG_COLOR: Record<FileTag, string> = {
  reused: "blue",
  customized: "gold",
  generated: "green",
};
export const TAG_HINT: Record<FileTag, string> = {
  reused: "Upstream repository code, untouched",
  customized: "A patch, or a repository file a patch changed",
  generated: "Written by SBAM's generator",
};
export const TAGS: FileTag[] = ["generated", "reused", "customized"];

export const filesOf = (groups: WorkflowCodeGroup[]): WorkflowCodeFile[] =>
  groups.flatMap((g) => g.files);

/** How many files carry each tag, over every group. */
export function tagCounts(groups: WorkflowCodeGroup[]): Record<FileTag, number> {
  const counts: Record<FileTag, number> = { reused: 0, customized: 0, generated: 0 };
  for (const f of filesOf(groups)) counts[f.tag] += 1;
  return counts;
}

/** The gates of a group that did not pass, by name. */
export const problemGates = (group: WorkflowCodeGroup): string[] =>
  group.gates.filter((g) => g.status === "FAILED").map((g) => g.gate);

/** A file name for the list: its last path segment, with the directory kept for the title. */
export function splitPath(path: string): { dir: string; name: string } {
  const i = path.lastIndexOf("/");
  return i < 0 ? { dir: "", name: path } : { dir: path.slice(0, i), name: path.slice(i + 1) };
}

/** The whole file as one range, so the line view shades nothing (nothing here is a model slot). */
export const wholeFile = (content: string) => {
  const n = content.endsWith("\n") ? content.split("\n").length - 1 : content.split("\n").length;
  return [{ start: 1, end: n }]; // an empty file is still one (empty) line
};
