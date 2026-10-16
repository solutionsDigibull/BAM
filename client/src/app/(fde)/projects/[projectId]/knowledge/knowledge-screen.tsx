"use client";

// The Knowledge screen (TD §9 "Knowledge Explorer"): the status and coverage panel (E5.6), then the
// read-only graph explorer (E5.5) for a READY or OUTDATED version. The explorer stays on the version it
// opened; when a newer one becomes READY it offers to switch instead of moving under the user.
import { useList } from "@refinedev/core";
import dynamic from "next/dynamic";
import { useState } from "react";

import { LoadingState } from "@/components/ui-kit";
import type { KnowledgeVersionSummary, Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { PROJECTS_RESOURCE } from "../../workspace-api";
import { KnowledgePanel } from "./knowledge-panel";

// Xyflow measures the DOM: client only.
const Explorer = dynamic(() => import("./explorer").then((m) => m.Explorer), {
  ssr: false,
  loading: () => <LoadingState />,
});

const EXPLORABLE = new Set(["READY", "OUTDATED"]);

export function KnowledgeScreen({ projectId }: { projectId: string }) {
  const projects = useList<Project, ApiError>({
    resource: PROJECTS_RESOURCE,
    pagination: { mode: "off" },
  });
  const project = projects.result.data?.find((p) => p.id === projectId);
  const [latest, setLatest] = useState<KnowledgeVersionSummary | undefined>();
  const [shown, setShown] = useState<{ id: string; version: number } | null>(null);

  const onVersion = (v: KnowledgeVersionSummary | undefined) => {
    setLatest(v);
    if (v && v.status === "READY" && !shown) setShown({ id: v.id, version: v.version });
  };
  const newer =
    latest && shown && latest.status === "READY" && latest.id !== shown.id
      ? { id: latest.id, version: latest.version }
      : null;

  return (
    <>
      <KnowledgePanel
        projectId={projectId}
        projectName={project?.name ?? "project"}
        onVersion={onVersion}
      />
      {shown && (EXPLORABLE.has(latest?.status ?? "") || newer) ? (
        <div style={{ marginTop: 16 }}>
          <Explorer
            key={shown.id}
            projectId={projectId}
            kvId={shown.id}
            newer={newer}
            onSwitch={(id) => latest && setShown({ id, version: latest.version })}
          />
        </div>
      ) : null}
    </>
  );
}
