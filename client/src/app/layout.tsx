import { AntdRegistry } from "@ant-design/nextjs-registry";
import type { Metadata } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { AppProviders } from "@/providers/app-providers";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "SBAM", template: "%s · SBAM" },
  description: "Skills Build Acceleration Manager",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  await connection(); // render per request: the CSP nonce cannot be stamped on a prerendered page
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
