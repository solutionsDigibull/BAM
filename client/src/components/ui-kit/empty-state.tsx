import { Empty, Skeleton } from "antd";
import type { ReactNode } from "react";

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <Empty description={message} style={{ padding: "48px 0" }}>
      {action}
    </Empty>
  );
}

export function LoadingState() {
  return <Skeleton active paragraph={{ rows: 6 }} />;
}
