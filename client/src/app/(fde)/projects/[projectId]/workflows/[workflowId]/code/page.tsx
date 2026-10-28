import { EmptyState } from "@/components/ui-kit";
import { isUuid } from "@/lib/roles";

import { CodeScreen } from "./code-screen";

export const metadata = { title: "Code" };

// The workflow layout has already rejected a malformed workflow id. `?sub=` names the Sub-Skill.
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
  if (!subId) return <EmptyState message="Choose a Sub-Skill to see its generated code" />;
  return <CodeScreen projectId={projectId} workflowId={workflowId} subId={subId} />;
}
