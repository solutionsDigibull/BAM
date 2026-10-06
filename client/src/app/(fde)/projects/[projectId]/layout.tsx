import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { projectNav } from "@/components/shell/nav";
import { SectionNav } from "@/components/shell/section-nav";
import { isUuid } from "@/lib/roles";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  if (!isUuid(projectId)) notFound(); // before any data fetch
  // The project name comes from the API once #45 lands; until then show the id.
  return (
    <>
      <SectionNav title={`Project ${projectId}`} items={projectNav(`/projects/${projectId}`)} />
      {children}
    </>
  );
}
