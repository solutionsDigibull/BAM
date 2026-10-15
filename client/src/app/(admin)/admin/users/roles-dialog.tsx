"use client";

import { useCustomMutation } from "@refinedev/core";
import { Alert, Button, Checkbox, Modal, Typography } from "antd";
import { useState } from "react";

import { ApiErrorState } from "@/components/ui-kit";
import type { TenantUser, UserPatch } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import {
  LAST_ADMIN_MESSAGE,
  ROLES,
  STALE_USER_MESSAGE,
  ifMatch,
  problemCode,
  statusOf,
  userLabel,
  userPath,
} from "./user-api";

/** PATCH a user with If-Match. No optimistic update: the caller refreshes the list on success. */
export function usePatchUser(user: TenantUser) {
  const [error, setError] = useState<ApiError | null>(null);
  const { mutateAsync, mutation } = useCustomMutation<TenantUser, ApiError>();
  const patch = async (values: UserPatch): Promise<TenantUser | null> => {
    setError(null);
    try {
      const res = await mutateAsync({
        url: userPath(user.id),
        method: "patch",
        values,
        config: { headers: ifMatch(user) },
        successNotification: false,
      });
      return res.data;
    } catch (e) {
      setError(e as ApiError);
      return null;
    }
  };
  return { patch, error, pending: mutation.isPending };
}

/** 409 LAST_ADMIN → banner; 412 → "changed by someone else" with Reload; anything else → ApiErrorState. */
export function PatchError({ error, onReload }: { error: ApiError | null; onReload: () => void }) {
  if (!error) return null;
  if (problemCode(error) === "LAST_ADMIN")
    return (
      <Alert
        type="warning"
        showIcon
        message={LAST_ADMIN_MESSAGE}
        description="Make another user an active admin first."
        style={{ marginBottom: 16 }}
      />
    );
  if (statusOf(error) === 412)
    return (
      <Alert
        type="info"
        showIcon
        message={STALE_USER_MESSAGE}
        description="Reload to see their current roles and status, then try again."
        action={
          <Button size="small" type="primary" onClick={onReload}>
            Reload
          </Button>
        }
        style={{ marginBottom: 16 }}
      />
    );
  return (
    <div style={{ marginBottom: 16 }}>
      <ApiErrorState error={error} />
    </div>
  );
}

export function RolesDialog({
  user,
  lastAdmin,
  onClose,
  onSaved,
  onReload,
}: {
  user: TenantUser;
  /** This user is the tenant's only active admin: the Admin role can't be removed. */
  lastAdmin: boolean;
  onClose: () => void;
  onSaved: (user: TenantUser) => void;
  onReload: () => void;
}) {
  const [roles, setRoles] = useState<string[]>(
    user.portal_roles.filter((r) => r !== "super-admin"),
  );
  const { patch, error, pending } = usePatchUser(user);
  const unchanged = [...roles].sort().join() === [...user.portal_roles].sort().join();

  const save = async () => {
    const saved = await patch({ roles: roles as NonNullable<UserPatch["roles"]> });
    if (saved) onSaved(saved);
  };

  return (
    <Modal
      open
      title="Change roles"
      okText="Save roles"
      okButtonProps={{ disabled: roles.length === 0 || unchanged }}
      confirmLoading={pending}
      onOk={save}
      onCancel={onClose}
      destroyOnHidden
    >
      <PatchError error={error} onReload={onReload} />
      <Typography.Paragraph>
        Roles for{" "}
        <Typography.Text strong ellipsis={{ tooltip: userLabel(user) }} style={{ maxWidth: 320 }}>
          {userLabel(user)}
        </Typography.Text>
        . They are signed out so the change applies at their next sign-in.
      </Typography.Paragraph>
      <Checkbox.Group
        value={roles}
        onChange={(v) => setRoles(v as string[])}
        options={ROLES.map((r) => ({
          value: r.value,
          label: r.label,
          disabled: lastAdmin && r.value === "sbam-admin",
        }))}
      />
      {lastAdmin && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          {LAST_ADMIN_MESSAGE}: this is the only active admin.
        </Typography.Paragraph>
      )}
    </Modal>
  );
}
