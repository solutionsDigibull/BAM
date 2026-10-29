import { isUuid } from "@/lib/roles";

import { ManifestScreen } from "./manifest-screen";

export const metadata = { title: "Manifests" };

// The workflow layout has already rejected a malformed workflow id. `?sub=` opens a Sub-Skill.
export default async function ManifestsPage({
  params,
  searchParams,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
  searchParams: Promise<{ sub?: string | string[] }>;
}) {
  const { projectId, workflowId } = await params;
  const { sub } = await searchParams;
  const subId = typeof sub === "string" && isUuid(sub) ? sub : null;
  return <ManifestScreen projectId={projectId} workflowId={workflowId} subId={subId} />;
}
