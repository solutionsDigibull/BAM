import { notFound } from "next/navigation";

import { isUuid } from "@/lib/roles";

import { ProjectMasters } from "../../project-masters";

export const metadata = { title: "Project masters" };

// Admin-only: (admin)/layout.tsx renders Forbidden for non-admins before this page (or its requests) runs.
export default async function ProjectMastersPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  if (!isUuid(projectId)) notFound();
  return <ProjectMasters projectId={projectId} />;
}
