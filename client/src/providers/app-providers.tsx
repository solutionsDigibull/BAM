"use client";

import "@ant-design/v5-patch-for-react-19";

import { Refine } from "@refinedev/core";
import routerProvider from "@refinedev/nextjs-router";
import { App, ConfigProvider, type ThemeConfig } from "antd";
import { Suspense, type ReactNode } from "react";

const theme: ThemeConfig = {
  token: {
    colorPrimary: "#4f46e5",
    borderRadius: 8,
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  },
  components: {
    Layout: { headerBg: "#ffffff", siderBg: "#0f172a", bodyBg: "#f6f7fb" },
    Menu: { darkItemBg: "#0f172a", darkSubMenuItemBg: "#0f172a" },
  },
};

// Data and access-control providers arrive with #45 (E1.14).
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider theme={theme}>
      <App>
        {/* nextjs-router reads useSearchParams(), which needs a Suspense boundary to prerender. */}
        <Suspense>
          <Refine routerProvider={routerProvider} options={{ disableTelemetry: true }}>
            {children}
          </Refine>
        </Suspense>
      </App>
    </ConfigProvider>
  );
}
