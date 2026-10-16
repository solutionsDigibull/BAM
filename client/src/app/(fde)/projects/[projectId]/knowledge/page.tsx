import { KnowledgeScreen } from "./knowledge-screen";

export const metadata = { title: "Knowledge" };

// The layout above has already rejected a malformed id (notFound) before this page or any request runs.
export default async function KnowledgePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <KnowledgeScreen projectId={projectId} />;
}
