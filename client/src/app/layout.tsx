import { AntdRegistry } from "@ant-design/nextjs-registry";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppProviders } from "@/providers/app-providers";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "SBAM", template: "%s · SBAM" },
  description: "Skills Build Acceleration Manager",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>
        <AntdRegistry>
          <AppProviders>{children}</AppProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}
