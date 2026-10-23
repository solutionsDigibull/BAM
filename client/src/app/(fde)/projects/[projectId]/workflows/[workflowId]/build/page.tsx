import { BuildScreen } from "./build-screen";

export const metadata = { title: "Build & Test" };

// The workflow layout has already rejected a malformed id (notFound) before this page or any request runs.
export default async function BuildTestPage({
  params,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
}) {
  const { projectId, workflowId } = await params;
  return <BuildScreen projectId={projectId} workflowId={workflowId} />;
}
