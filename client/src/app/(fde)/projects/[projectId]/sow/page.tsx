import { SowScreen } from "./sow-screen";

export const metadata = { title: "SOW" };

// The layout above has already rejected a malformed id (notFound) before this page or any request runs.
export default async function SowPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <SowScreen projectId={projectId} />;
}
