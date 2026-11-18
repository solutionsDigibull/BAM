import { isUuid } from "@/lib/roles";

import { CodeScreen } from "./code-screen";
import { WorkflowCodeScreen } from "./workflow-code-screen";

export const metadata = { title: "Code" };

// The workflow layout has already rejected a malformed workflow id. `?sub=` names a Sub-Skill and opens its
// attempts, files and diffs; without it the whole Workflow's code is shown, a tag per file (WG-15).
export default async function CodePage({
  params,
  searchParams,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
  searchParams: Promise<{ sub?: string | string[] }>;
}) {
  const { projectId, workflowId } = await params;
  const { sub } = await searchParams;
  const subId = typeof sub === "string" && isUuid(sub) ? sub : null;
  if (!subId) return <WorkflowCodeScreen projectId={projectId} workflowId={workflowId} />;
  return <CodeScreen projectId={projectId} workflowId={workflowId} subId={subId} />;
}
