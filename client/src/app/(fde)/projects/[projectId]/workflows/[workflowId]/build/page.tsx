import { BuildScreen } from "./build-screen";

export const metadata = { title: "Build & Test" };

// The workflow layout has already rejected a malformed id (notFound) before this page or any request runs.
// `?view=matrix` opens the every-gate matrix instead of the summary cards.
export default async function BuildTestPage({
  params,
  searchParams,
}: {
  params: Promise<{ projectId: string; workflowId: string }>;
  searchParams: Promise<{ view?: string }>;
}) {
  const { projectId, workflowId } = await params;
  const { view } = await searchParams;
  return (
    <BuildScreen
      projectId={projectId}
      workflowId={workflowId}
      initialView={view === "matrix" ? "matrix" : "cards"}
    />
  );
}
