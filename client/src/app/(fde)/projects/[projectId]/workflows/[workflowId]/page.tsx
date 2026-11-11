import { WorkflowOverview } from "./workflow-overview";

export const metadata = { title: "Workflow overview" };

export default async function WorkflowOverviewPage({
  params,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
}) {
  const { projectId, workflowId } = await params;
  return <WorkflowOverview projectId={projectId} workflowId={workflowId} />;
}
