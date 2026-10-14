"use client";

import { UserAddOutlined } from "@ant-design/icons";
import { useCustom, useCustomMutation } from "@refinedev/core";
import { Alert, Avatar, Button, Drawer, Flex, Select, Space, Typography } from "antd";
import { useMemo, useState } from "react";

import { ApiErrorState, EmptyState, LoadingState } from "@/components/ui-kit";
import type { Project } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  ARCHIVED_MESSAGE,
  MAX_MEMBERS,
  USERS_PATH,
  memberIds,
  membersPath,
  selectableFdes,
  userLabel,
  type Members,
  type TenantUser,
} from "./project-api";

const initials = (name: string) =>
  name
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");

/** Assign the tenant's active FDEs to a project (replace semantics, PUT members). */
export function MembersDrawer({
  project,
  onClose,
  onSaved,
}: {
  project: Project;
  onClose: () => void;
  onSaved: () => void;
}) {
  const users = useCustom<{ items: TenantUser[] }, ApiError>({
    url: USERS_PATH,
    method: "get",
    config: { query: { role: "sbam-fde", active: "true" } },
  });
  const current = useCustom<Members, ApiError>({ url: membersPath(project.id), method: "get" });
  const save = useCustomMutation<Members, ApiError>();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const fdes = useMemo(() => selectableFdes(users.result.data?.items ?? []), [users.result.data]);
  const assigned = useMemo(() => memberIds(current.result.data), [current.result.data]);
  const value = selected ?? assigned; // the saved members until the admin changes the selection

  const archived = project.status === "ARCHIVED";
  const loading = users.query.isLoading || current.query.isLoading;
  const loadError = users.query.error ?? current.query.error;
  const byId = new Map(fdes.map((u) => [u.id, u]));

  const submit = async () => {
    setError(null);
    try {
      await save.mutateAsync({
        url: membersPath(project.id),
        method: "put",
        values: { user_ids: value },
      });
      onSaved();
    } catch (e) {
      setError(e as ApiError);
    }
  };

  let body;
  if (loading) body = <LoadingState />;
  else if (loadError)
    body = (
      <ApiErrorState
        error={loadError}
        onRetry={() => {
          void users.query.refetch();
          void current.query.refetch();
        }}
      />
    );
  else if (fdes.length === 0)
    body = (
      <EmptyState
        message="No active FDEs in this tenant yet."
        action={
          <Button type="primary" icon={<UserAddOutlined />} href="/admin/users">
            Invite an FDE
          </Button>
        }
      />
    );
  else
    body = (
      <>
        <Typography.Paragraph type="secondary">
          Assigned FDEs see this project in their portal. Removing someone takes their access away
          on their next request.
        </Typography.Paragraph>
        <Select
          mode="multiple"
          aria-label="Assigned FDEs"
          style={{ width: "100%" }}
          placeholder="Search FDEs by name or email"
          value={value}
          onChange={setSelected}
          maxCount={MAX_MEMBERS}
          disabled={archived}
          optionFilterProp="search"
          options={fdes.map((u) => ({
            value: u.id,
            label: userLabel(u),
            search: `${u.display_name ?? ""} ${u.email ?? ""}`,
          }))}
          optionRender={(option) => {
            const u = byId.get(String(option.value));
            return (
              <Flex gap={8} align="center">
                <Avatar size="small">{initials(String(option.label))}</Avatar>
                <span>{option.label}</span>
                {u?.email && u.email !== option.label && (
                  <Typography.Text type="secondary">{u.email}</Typography.Text>
                )}
              </Flex>
            );
          }}
        />
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>
          {value.length} of at most {MAX_MEMBERS} assigned
        </Typography.Text>
      </>
    );

  return (
    <Drawer
      open
      width={520}
      title={
        <Typography.Text ellipsis={{ tooltip: project.name }} style={{ maxWidth: 380 }}>
          FDEs · {project.name}
        </Typography.Text>
      }
      onClose={onClose}
      destroyOnHidden
      extra={
        <Space>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            type="primary"
            onClick={submit}
            loading={save.mutation.isPending}
            disabled={archived || loading || !!loadError || fdes.length === 0}
          >
            Save
          </Button>
        </Space>
      }
    >
      {archived && (
        <Alert type="info" showIcon message={ARCHIVED_MESSAGE} style={{ marginBottom: 16 }} />
      )}
      {error && (
        <div style={{ marginBottom: 16 }}>
          <ApiErrorState error={error} onRetry={submit} />
        </div>
      )}
      {body}
    </Drawer>
  );
}
