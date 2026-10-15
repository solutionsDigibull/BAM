"use client";

// Layout follows the UI team's admin reference (docs/reference/ui/README.md, "Shared patterns"), like
// Admin Projects: title + description with the primary action on the right, tabs with count badges,
// search + "n of m rows", a bordered grid with small upper-case headers, and a footer line.

import { PlusOutlined, StopOutlined, UndoOutlined, UserSwitchOutlined } from "@ant-design/icons";
import { useCustom, useList } from "@refinedev/core";
import {
  App,
  Button,
  ConfigProvider,
  Flex,
  Input,
  Space,
  Table,
  Tabs,
  Tag,
  theme,
  Tooltip,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useMemo, useState, type ReactNode } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { TenantUser, UserStatus } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { DeactivateDialog } from "./deactivate-dialog";
import { InviteDialog } from "./invite-dialog";
import { RolesDialog } from "./roles-dialog";
import {
  EMPTY_MESSAGE,
  LAST_ADMIN_MESSAGE,
  PAGE_SIZE,
  USERS,
  isActiveAdmin,
  roleLabel,
  userLabel,
} from "./user-api";

type Tab = "ALL" | UserStatus;
const TABS: { key: Tab; label: string }[] = [
  { key: "ALL", label: "All users" },
  { key: "ACTIVE", label: "Active" },
  { key: "INVITED", label: "Invited" },
  { key: "DEACTIVATED", label: "Deactivated" },
];
const STATUS_COLOR: Record<UserStatus, string> = {
  ACTIVE: "green",
  INVITED: "gold",
  DEACTIVATED: "default",
};

const MUTED = "#64748b";
const head = (label: string) => (
  <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: 0.3, color: MUTED }}>
    {label.toUpperCase()}
  </span>
);

function CountBadge({ n, active }: { n: number; active: boolean }) {
  const { token } = theme.useToken();
  return (
    <span
      style={{
        marginLeft: 6,
        padding: "0 7px",
        borderRadius: 10,
        fontSize: 11,
        lineHeight: "18px",
        display: "inline-block",
        background: active ? token.colorPrimaryBg : "#f1f5f9",
        color: active ? token.colorPrimary : MUTED,
      }}
    >
      {n}
    </span>
  );
}

function Meta({ children }: { children: ReactNode }) {
  return <Typography.Text style={{ fontSize: 12, color: MUTED }}>{children}</Typography.Text>;
}

type Action = { kind: "roles" | "active"; user: TenantUser };

export function UsersScreen() {
  const { message } = App.useApp();
  const [tab, setTab] = useState<Tab>("ALL");
  const [search, setSearch] = useState("");
  const [inviting, setInviting] = useState(false);
  const [action, setAction] = useState<Action | null>(null);
  // Cursor pages of 100, oldest first. ponytail: Previous/Next over a cursor stack; tabs and search
  // work on the page shown, which is the whole tenant until it has more than 100 users.
  const [cursors, setCursors] = useState<string[]>([]);

  const { result, query } = useList<TenantUser, ApiError>({
    resource: USERS,
    pagination: { mode: "server", pageSize: PAGE_SIZE },
    meta: { cursor: cursors.at(-1) },
  });
  // The last-admin guard needs the tenant-wide count, not just this page's.
  const admins = useCustom<{ items: TenantUser[] }, ApiError>({
    url: USERS,
    method: "get",
    config: { query: { role: "sbam-admin", active: "true", limit: "2" } },
  });
  const users = useMemo(() => result.data ?? [], [result.data]);
  const nextCursor = query.data?.cursor?.next as string | undefined;
  // Filtered again here, so the guard holds even if the API ignored the query.
  const soleAdmin = (admins.result.data?.items ?? []).filter(isActiveAdmin).length <= 1;
  const isLastAdmin = (u: TenantUser) => isActiveAdmin(u) && soleAdmin;

  const refresh = () => {
    void query.refetch();
    void admins.query.refetch();
  };

  const counts = useMemo(() => {
    const by = (s: UserStatus) => users.filter((u) => u.status === s).length;
    return {
      ALL: users.length,
      ACTIVE: by("ACTIVE"),
      INVITED: by("INVITED"),
      DEACTIVATED: by("DEACTIVATED"),
    };
  }, [users]);
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return users
      .filter((u) => tab === "ALL" || u.status === tab)
      .filter(
        (u) =>
          !needle ||
          (u.email ?? "").toLowerCase().includes(needle) ||
          (u.display_name ?? "").toLowerCase().includes(needle),
      );
  }, [users, tab, search]);

  const inviteButton = (
    <Button type="primary" icon={<PlusOutlined />} onClick={() => setInviting(true)}>
      Invite user
    </Button>
  );

  const actions = (u: TenantUser) => {
    const last = isLastAdmin(u);
    const deactivated = u.status === "DEACTIVATED";
    return (
      <Flex gap={2} justify="flex-end">
        <Button
          size="small"
          type="text"
          icon={<UserSwitchOutlined />}
          aria-label={`Change roles of ${userLabel(u)}`}
          onClick={() => setAction({ kind: "roles", user: u })}
        >
          Roles
        </Button>
        <Tooltip title={last && !deactivated ? `${LAST_ADMIN_MESSAGE}.` : undefined}>
          <Button
            size="small"
            type="text"
            danger={!deactivated}
            disabled={last && !deactivated}
            icon={deactivated ? <UndoOutlined /> : <StopOutlined />}
            aria-label={`${deactivated ? "Reactivate" : "Deactivate"} ${userLabel(u)}`}
            onClick={() => setAction({ kind: "active", user: u })}
          >
            {deactivated ? "Reactivate" : "Deactivate"}
          </Button>
        </Tooltip>
      </Flex>
    );
  };

  const grid = (
    <>
      <Flex justify="space-between" align="center" wrap gap={12} style={{ margin: "16px 0" }}>
        <Input
          allowClear
          placeholder="Search by name or email..."
          aria-label="Search users"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 340, maxWidth: "100%" }}
        />
        <Meta>
          {rows.length} of {counts[tab]} rows
        </Meta>
      </Flex>
      <ConfigProvider
        theme={{
          components: {
            Table: {
              headerBg: "#f8fafc",
              headerSplitColor: "transparent",
              borderColor: "#e5e7eb",
              rowHoverBg: "#f5f9ff",
              cellPaddingBlock: 14,
            },
          },
        }}
      >
        <Table<TenantUser>
          rowKey="id"
          bordered={false}
          dataSource={rows}
          pagination={rows.length > 25 ? { pageSize: 25, showSizeChanger: false } : false}
          scroll={{ x: 900 }}
          style={{ border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}
          locale={{
            emptyText: (
              <EmptyState message={search ? "No users match your search." : "No users here."} />
            ),
          }}
          columns={[
            {
              title: head("User"),
              key: "user",
              render: (_, u) => (
                // Plain text nodes only: names and emails can't inject markup (E2.6-T07).
                <Flex vertical style={{ maxWidth: 340 }}>
                  <Typography.Text strong ellipsis={{ tooltip: u.display_name ?? undefined }}>
                    {u.display_name || "—"}
                  </Typography.Text>
                  <Typography.Text
                    type="secondary"
                    style={{ fontSize: 12 }}
                    ellipsis={{ tooltip: u.email ?? undefined }}
                  >
                    {u.email ?? "—"}
                  </Typography.Text>
                </Flex>
              ),
            },
            {
              title: head("Roles"),
              key: "roles",
              width: 200,
              render: (_, u) => (
                <Space size={4} wrap data-testid={`roles-${u.id}`}>
                  {u.portal_roles.map((r) => (
                    <Tag key={r} color={r === "sbam-admin" ? "blue" : "cyan"}>
                      {roleLabel(r)}
                    </Tag>
                  ))}
                </Space>
              ),
            },
            {
              title: head("Status"),
              key: "status",
              width: 140,
              render: (_, u) => (
                <Tag color={STATUS_COLOR[u.status]} data-status={u.status}>
                  {u.status.charAt(0) + u.status.slice(1).toLowerCase()}
                </Tag>
              ),
            },
            {
              title: head("Last login"),
              key: "last_login",
              width: 170,
              render: (_, u) =>
                u.last_login_at ? (
                  <Tooltip title={dayjs(u.last_login_at).format("YYYY-MM-DD HH:mm:ss")}>
                    <Meta>{dayjs(u.last_login_at).format("D MMM YYYY, HH:mm")}</Meta>
                  </Tooltip>
                ) : (
                  <Meta>Never</Meta>
                ),
            },
            {
              title: head("Actions"),
              key: "actions",
              width: 230,
              align: "right",
              render: (_, u) => actions(u),
            },
          ]}
        />
      </ConfigProvider>
      <Flex justify="space-between" wrap gap={8} style={{ marginTop: 12 }}>
        <Meta>Oldest first · invited users become active at their first sign-in</Meta>
        {(cursors.length > 0 || nextCursor) && (
          <Space>
            <Button
              size="small"
              disabled={!cursors.length}
              onClick={() => setCursors((c) => c.slice(0, -1))}
            >
              Previous {PAGE_SIZE}
            </Button>
            <Button
              size="small"
              disabled={!nextCursor}
              onClick={() => nextCursor && setCursors((c) => [...c, nextCursor])}
            >
              Next {PAGE_SIZE}
            </Button>
          </Space>
        )}
      </Flex>
    </>
  );

  let body;
  if (query.isLoading) body = <LoadingState />;
  else if (query.isError) body = <ApiErrorState error={query.error} onRetry={refresh} />;
  else
    body = (
      <>
        {/* Only the signed-in admin so far: the table still shows, with the next step called out. */}
        {users.length <= 1 && cursors.length === 0 && (
          <EmptyState message={EMPTY_MESSAGE} action={inviteButton} />
        )}
        <Tabs
          activeKey={tab}
          destroyOnHidden
          onChange={(key) => setTab(key as Tab)}
          items={TABS.map(({ key, label }) => ({
            key,
            label: (
              <span>
                {label}
                <CountBadge n={counts[key]} active={tab === key} />
              </span>
            ),
            children: grid,
          }))}
        />
      </>
    );

  return (
    <div>
      <Flex justify="space-between" align="flex-start" wrap gap={12} style={{ marginBottom: 20 }}>
        <div>
          <Typography.Title level={1} style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>
            Users &amp; Roles
          </Typography.Title>
          <Typography.Text style={{ color: MUTED }}>
            Invite admins and FDEs to your tenant, change their roles, or deactivate them
          </Typography.Text>
        </div>
        {inviteButton}
      </Flex>
      {body}

      {inviting && (
        <InviteDialog
          onClose={() => setInviting(false)}
          onInvited={(u) => {
            setInviting(false);
            refresh();
            message.success(`Invite sent to ${u.email}`);
          }}
        />
      )}
      {action?.kind === "roles" && (
        <RolesDialog
          user={action.user}
          lastAdmin={isLastAdmin(action.user)}
          onClose={() => setAction(null)}
          onReload={() => {
            setAction(null);
            refresh();
          }}
          onSaved={(u) => {
            setAction(null);
            refresh();
            message.success(`Saved roles of ${userLabel(u)}`);
          }}
        />
      )}
      {action?.kind === "active" && (
        <DeactivateDialog
          user={action.user}
          onClose={() => setAction(null)}
          onReload={() => {
            setAction(null);
            refresh();
          }}
          onSaved={(u) => {
            setAction(null);
            refresh();
            message.success(
              `${u.status === "DEACTIVATED" ? "Deactivated" : "Reactivated"} ${userLabel(u)}`,
            );
          }}
        />
      )}
    </div>
  );
}
