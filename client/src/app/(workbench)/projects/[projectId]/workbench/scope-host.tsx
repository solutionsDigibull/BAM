"use client";

// The Scope screen (workbench step 3.1) is the SOW screen of the FDE portal: it edits the strict TipTap document the
// backend saves (ADR 0019) and already has versions, diff, impact, citations and Approve. The workbench draws an empty
// slot in its centre pane; this renders the screen into that slot through a portal, so it keeps the app's data
// provider (the workbench nests its own) and takes on the workbench's light or dark look.
import { createPortal } from "react-dom";

import { SowScreen } from "@/app/(fde)/projects/[projectId]/sow/sow-screen";
import { useSlotElement } from "@/workbench/host/slots";

import { HostTheme } from "./host-theme";

export function ScopeHost({ projectId }: { projectId: string }) {
  const slot = useSlotElement("scope");
  if (!slot) return null;
  return createPortal(
    <HostTheme>
      <div className="scope-host-body">
        <SowScreen projectId={projectId} />
      </div>
    </HostTheme>,
    slot,
  );
}
