"use client";

import { BellOutlined } from "@ant-design/icons";
import { Badge, Button } from "antd";

// The inbox drawer and live count arrive with the notifications API (TD §8.13).
export function NotificationBell({ count = 0, onOpen }: { count?: number; onOpen?: () => void }) {
  return (
    <Badge count={count} size="small" overflowCount={99}>
      <Button
        type="text"
        shape="circle"
        aria-label={`Notifications (${count} unread)`}
        icon={<BellOutlined style={{ fontSize: 18 }} />}
        onClick={onOpen}
      />
    </Badge>
  );
}
