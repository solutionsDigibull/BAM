"use client"; // antd compound members (Typography.Title) only resolve in client components

import { Card, Typography } from "antd";

import { EmptyState } from "@/components/ui-kit";

/** Empty page frame: the issue that builds this screen replaces the EmptyState body. */
export function PagePlaceholder({ title, empty }: { title: string; empty: string }) {
  return (
    <Card>
      <Typography.Title level={3} ellipsis={{ tooltip: title }} style={{ marginTop: 0 }}>
        {title}
      </Typography.Title>
      <EmptyState message={empty} />
    </Card>
  );
}
