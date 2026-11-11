"use client";

// The Grid screen (workbench step 3.2) is the ISATVON grid of the FDE portal: Macro and Sub-Skills with every ISATVON
// field, edit (If-Match), validate, regenerate, delete, export and Generate (which starts the build). The workbench
// draws an empty slot in its centre pane; this renders the screen into it through a portal, so it keeps the app's data
// provider and takes on the workbench's look. SBAM creates the Workflow when the SOW is approved, so until then there
// is nothing to show here; the FDE never creates one.
import { useCustom } from "@refinedev/core";
import { createPortal } from "react-dom";

import { workflowsPath } from "@/app/(fde)/projects/workspace-api";
import { WorkbenchScreen } from "@/app/(fde)/projects/[projectId]/workflows/[workflowId]/isatvon/workbench-screen";
import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { WorkflowSummary } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";
import { useSlotElement } from "@/workbench/host/slots";

import { HostTheme } from "./host-theme";

export const NO_WORKFLOW =
  "The grid appears after the SOW is approved. SBAM creates the workflow from it.";

/** The workflow to show: the newest the project has (SBAM makes one per approved SOW). */
export const currentWorkflow = (
  items: WorkflowSummary[] | undefined,
): WorkflowSummary | undefined =>
  [...(items ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

function Grid({ projectId }: { projectId: string }) {
  const list = useCustom<{ items: WorkflowSummary[] }, ApiError>({
    url: workflowsPath(projectId),
    method: "get",
    // A workflow appears a moment after the SOW is approved: keep asking while there is none.
    queryOptions: {
      refetchInterval: (q) => (currentWorkflow(q.state.data?.data?.items) ? false : 5_000),
    },
  });
  if (list.query.isLoading) return <LoadingState />;
  if (list.query.error)
    return <ApiErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />;
  const workflow = currentWorkflow(list.result.data?.items);
  if (!workflow) return <EmptyState message={NO_WORKFLOW} />;
  return <WorkbenchScreen projectId={projectId} workflowId={workflow.id} />;
}

export function GridHost({ projectId }: { projectId: string }) {
  const slot = useSlotElement("grid");
  if (!slot) return null;
  return createPortal(
    <HostTheme>
      <div className="scope-host-body">
        <Grid projectId={projectId} />
      </div>
    </HostTheme>,
    slot,
  );
}
