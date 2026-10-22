"use client";

// Notification bell + inbox (E10.5): unread badge, polling, mark-read (single + all) and deep links.
// The backend is authoritative on link safety (TD §8.13); this also re-checks before navigating.
import { useCustom, useCustomMutation } from "@refinedev/core";
import { App, Button, Drawer, Empty, List, Tag, Typography } from "antd";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { useRouter } from "next/navigation";
import { useState } from "react";

dayjs.extend(relativeTime);

import type { Notification } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { ApiErrorState } from "./api-error-state";
import { LoadingState } from "./empty-state";
import { NotificationBell } from "./notification-bell";
import {
  EMPTY_INBOX,
  MARK_READ_FAILED,
  POLL_MS,
  UNREAD_PAGE_LIMIT,
  notificationsPath,
  readAllPath,
  readPath,
  safeLink,
  typeLabel,
  type NotificationPage,
} from "./notifications-api";

const when = (value: string) => dayjs(value).fromNow();

export function NotificationCenter() {
  const { message } = App.useApp();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // Optimistic overlay on top of the server's `read_at`: set immediately on click, reverted on failure.
  const [locallyRead, setLocallyRead] = useState<ReadonlySet<string>>(new Set());

  const list = useCustom<NotificationPage, ApiError>({
    url: notificationsPath(),
    method: "get",
    config: { query: { limit: UNREAD_PAGE_LIMIT } },
    queryOptions: {
      // Polls only while this component is mounted — unmounted (e.g. after logout) means no more requests.
      refetchInterval: POLL_MS,
      refetchIntervalInBackground: false,
    },
  });

  const markOne = useCustomMutation<Record<string, never>, ApiError>();
  const markAll = useCustomMutation<Record<string, never>, ApiError>();

  const items = list.result.data?.items ?? [];
  const isRead = (n: Notification) => Boolean(n.read_at) || locallyRead.has(n.id);
  const unreadCount = items.filter((n) => !isRead(n)).length;

  const revert = (id: string) =>
    setLocallyRead((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });

  const markRead = (n: Notification) => {
    if (isRead(n)) return;
    setLocallyRead((prev) => new Set(prev).add(n.id));
    markOne.mutate(
      { url: readPath(n.id), method: "post", values: {} },
      {
        onError: () => {
          revert(n.id);
          void message.error(MARK_READ_FAILED);
        },
        onSettled: () => void list.query.refetch(),
      },
    );
  };

  const markAllRead = () => {
    const ids = items.filter((n) => !isRead(n)).map((n) => n.id);
    if (ids.length === 0) return;
    setLocallyRead((prev) => new Set([...prev, ...ids]));
    markAll.mutate(
      { url: readAllPath(), method: "post", values: {} },
      {
        onError: () => {
          setLocallyRead((prev) => {
            const next = new Set(prev);
            for (const id of ids) next.delete(id);
            return next;
          });
          void message.error(MARK_READ_FAILED);
        },
        onSettled: () => void list.query.refetch(),
      },
    );
  };

  const onItemClick = (n: Notification) => {
    markRead(n);
    const link = safeLink(n.link);
    if (link) {
      setOpen(false);
      router.push(link);
    }
  };

  return (
    <>
      <NotificationBell count={unreadCount} onOpen={() => setOpen(true)} />
      <Drawer title="Notifications" open={open} onClose={() => setOpen(false)} width={420}>
        {list.query.isLoading ? <LoadingState /> : null}
        {list.query.error ? (
          <ApiErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : null}
        {!list.query.isLoading && !list.query.error ? (
          items.length === 0 ? (
            <Empty description={EMPTY_INBOX} style={{ padding: "48px 0" }} />
          ) : (
            <>
              <Button
                type="link"
                disabled={unreadCount === 0}
                onClick={markAllRead}
                style={{ marginBottom: 8, paddingLeft: 0 }}
              >
                Mark all read
              </Button>
              <List
                dataSource={items}
                renderItem={(n) => {
                  const link = safeLink(n.link);
                  const read = isRead(n);
                  return (
                    <List.Item
                      key={n.id}
                      onClick={() => onItemClick(n)}
                      style={{
                        cursor: "pointer",
                        background: read ? undefined : "rgba(22, 119, 255, 0.06)",
                        paddingInline: 8,
                      }}
                    >
                      <List.Item.Meta
                        title={
                          <Typography.Text strong={!read}>
                            {n.title}
                            {!read ? (
                              <Tag color="blue" style={{ marginLeft: 8 }}>
                                New
                              </Tag>
                            ) : null}
                          </Typography.Text>
                        }
                        description={
                          <>
                            <Typography.Paragraph
                              style={{ marginBottom: 4 }}
                              ellipsis={{ rows: 2 }}
                            >
                              {n.body}
                            </Typography.Paragraph>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {typeLabel(n.type)} · {when(n.created_at)}
                              {link === null && n.link ? " · link unavailable" : ""}
                            </Typography.Text>
                          </>
                        }
                      />
                    </List.Item>
                  );
                }}
              />
            </>
          )
        ) : null}
      </Drawer>
    </>
  );
}
