"use client";

// The look host screens take on inside the workbench: its light or dark colours as an antd theme. Shared by the Scope
// (SOW) host and the Grid host, so both read as one app.
import { ConfigProvider, theme } from "antd";
import type { ReactNode } from "react";

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

export function HostTheme({ children }: { children: ReactNode }) {
  const mode = useUi((s) => s.theme);
  return (
    <ConfigProvider
      theme={{
        algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: { ...TOKENS[mode], borderRadius: 10, fontFamily: "inherit" },
        components: { Card: { colorBgContainer: TOKENS[mode].colorBgContainer } },
      }}
    >
      {children}
    </ConfigProvider>
  );
}
