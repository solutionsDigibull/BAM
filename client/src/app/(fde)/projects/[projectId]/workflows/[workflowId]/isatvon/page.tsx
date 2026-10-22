import { WorkbenchScreen } from "./workbench-screen";

export const metadata = { title: "ISATVON Workbench" };

// The workflow layout has already rejected a malformed id (notFound) before this page or any request runs.
export default async function IsatvonWorkbenchPage({
  params,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
}) {
  const { projectId, workflowId } = await params;
  return <WorkbenchScreen projectId={projectId} workflowId={workflowId} />;
}
