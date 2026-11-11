import { WorkflowsScreen } from "./workflows-screen";

export const metadata = { title: "Workflows" };

export default async function WorkflowsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <WorkflowsScreen projectId={projectId} />;
}
