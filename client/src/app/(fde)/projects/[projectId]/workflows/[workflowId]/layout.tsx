import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { workflowNav } from "@/components/shell/nav";
import { SectionNav } from "@/components/shell/section-nav";
import { isUuid } from "@/lib/roles";

export default async function WorkflowLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ projectId: string; workflowId: string }>;
}) {
  const { projectId, workflowId } = await params;
  if (!isUuid(workflowId)) notFound();
  return (
    <>
      <SectionNav
        title={`Workflow ${workflowId}`}
        items={workflowNav(`/projects/${projectId}/workflows/${workflowId}`)}
      />
      {children}
    </>
  );
}
