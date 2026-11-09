"use client";

// The Scope screen (workbench step 3.1) is the SOW screen of the FDE portal: it edits the strict TipTap document the
// backend saves (ADR 0019) and already has versions, diff, impact, citations and Approve. The workbench draws an empty
// slot in its centre pane; this renders the screen into that slot through a portal, so it keeps the app's data
// provider (the workbench nests its own) and takes on the workbench's light or dark look.
import { ConfigProvider, theme } from "antd";
import { createPortal } from "react-dom";

import { SowScreen } from "@/app/(fde)/projects/[projectId]/sow/sow-screen";
import { useSlotElement } from "@/workbench/host/slots";
import { useUi } from "@/workbench/lib/store";

// The workbench tokens (workbench/styles.css) as the concrete colours antd needs: it derives shades from them, so
// CSS variables cannot be used here.
const TOKENS = {
  dark: {
    colorPrimary: "#5B83F2",
    colorBgBase: "#0B1526",
    colorBgContainer: "#101E32",
    colorBgElevated: "#142540",
    colorBorder: "#22395C",
    colorText: "#E6EDF7",
    colorTextSecondary: "#93A6C2",
  },
  light: {
    colorPrimary: "#006C99",
    colorBgBase: "#FFFFFF",
    colorBgContainer: "#FFFFFF",
    colorBgElevated: "#FFFFFF",
    colorBorder: "#DCE3EE",
    colorText: "#0F1B2D",
    colorTextSecondary: "#55667F",
  },
} as const;

export function ScopeHost({ projectId }: { projectId: string }) {
  const slot = useSlotElement("scope");
  const mode = useUi((s) => s.theme);
  if (!slot) return null;
  return createPortal(
    <ConfigProvider
      theme={{
        algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: { ...TOKENS[mode], borderRadius: 10, fontFamily: "inherit" },
        components: { Card: { colorBgContainer: TOKENS[mode].colorBgContainer } },
      }}
    >
      <div className="scope-host-body">
        <SowScreen projectId={projectId} />
      </div>
    </ConfigProvider>,
    slot,
  );
}
