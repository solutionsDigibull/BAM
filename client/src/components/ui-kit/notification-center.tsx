"use client";

// Notification bell + inbox (E10.5): unread badge, polling, mark-read (single + all) and deep links.
// The backend is authoritative on link safety (TD §8.13); this also re-checks before navigating.
import { useCustomMutation } from "@refinedev/core";
import { useQuery } from "@tanstack/react-query";
import { App, Button, Drawer, Empty, List, Tag, Typography } from "antd";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { useRouter } from "next/navigation";
import { useState } from "react";

dayjs.extend(relativeTime);

import type { Notification } from "@/contracts";
import { API_BASE, toApiError, type ApiError } from "@/providers/data-provider";

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

  // Plain react-query, not Refine's useCustom: a background poll failing (e.g. a transient 401)
  // must never trigger Refine's global auth-error redirect and log the user out from under them.
  const list = useQuery<NotificationPage, ApiError>({
    queryKey: ["notification-center", notificationsPath()],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/${notificationsPath()}?limit=${UNREAD_PAGE_LIMIT}`, {
        headers: { accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw await toApiError(res);
      return (await res.json()) as NotificationPage;
    },
    // One fetch on mount always populates the badge; the repeating interval only runs while the
    // drawer is open, so an idle page isn't hitting this endpoint forever in the background.
    refetchInterval: open ? POLL_MS : false,
    refetchIntervalInBackground: false,
    // A failed poll doesn't need its own retry/backoff: the next cycle already is one.
    retry: false,
  });

  const markOne = useCustomMutation<Record<string, never>, ApiError>();
  const markAll = useCustomMutation<Record<string, never>, ApiError>();

  const items = list.data?.items ?? [];
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
        onSettled: () => void list.refetch(),
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
        onSettled: () => void list.refetch(),
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
        {list.isLoading ? <LoadingState /> : null}
        {list.error ? (
          <ApiErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : null}
        {!list.isLoading && !list.error ? (
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
