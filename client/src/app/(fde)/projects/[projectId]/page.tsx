import { OverviewScreen } from "./overview-screen";

export const metadata = { title: "Project overview" };

// The layout above has already rejected a malformed id (notFound) before this page or any request runs.
export default async function OverviewPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <OverviewScreen projectId={projectId} />;
}
