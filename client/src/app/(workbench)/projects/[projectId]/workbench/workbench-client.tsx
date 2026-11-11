"use client";

import dynamic from "next/dynamic";
import { useLayoutEffect, useState } from "react";

import { setBamApi } from "@/workbench/api/adapter";
import { setHostSession } from "@/workbench/features/login/session";
import { createSbamApi } from "@/workbench/http/sbamApi";

import { GridHost } from "./grid-host";
import { ScopeHost } from "./scope-host";

// The workbench uses browser-only APIs (Uppy, canvas, ResizeObserver), so it is not rendered on the server.
const Workbench = dynamic(() => import("@/workbench/Workbench").then((m) => m.Workbench), {
  ssr: false,
  loading: () => <p style={{ padding: 24 }}>Loading the workbench...</p>,
});

export function WorkbenchClient({
  projectId,
  user,
  signOut,
}: {
  projectId: string;
  user: string;
  signOut: () => Promise<void>;
}) {
  const [api] = useState(() => createSbamApi(projectId));
  // Layout effects run before any query starts, so the API and the session are in place for the first poll.
  useLayoutEffect(() => {
    setBamApi(api);
    setHostSession({ email: user, signOut: () => void signOut() });
    return () => {
      setBamApi(null);
      setHostSession({ email: null, signOut: () => {} });
    };
  }, [api, user, signOut]);
  return (
    <div style={{ height: "100vh" }}>
      <Workbench />
      <ScopeHost projectId={projectId} />
      <GridHost projectId={projectId} />
    </div>
  );
}
