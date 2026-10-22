import { DocumentsScreen } from "./documents-screen";

export const metadata = { title: "Documents" };

// The layout above has already rejected a malformed id (notFound) before this page or any request runs.
export default async function DocumentsPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <DocumentsScreen projectId={projectId} />;
}
