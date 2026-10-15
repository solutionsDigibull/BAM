"use client";

import { Input, Modal, Typography } from "antd";
import { useState } from "react";

import type { TenantUser } from "@/contracts";

import { PatchError, usePatchUser } from "./roles-dialog";
import { userLabel } from "./user-api";

/** Deactivate needs the user's email typed back; reactivate is a plain confirm. The API decides
 *  (409 LAST_ADMIN, 412 when the row changed). */
export function DeactivateDialog({
  user,
  onClose,
  onSaved,
  onReload,
}: {
  user: TenantUser;
  onClose: () => void;
  onSaved: (user: TenantUser) => void;
  onReload: () => void;
}) {
  const reactivate = user.status === "DEACTIVATED";
  const [typed, setTyped] = useState("");
  const { patch, error, pending } = usePatchUser(user);
  const target = user.email ?? userLabel(user); // email, or the name for a user without one
  const confirmed = reactivate || typed.trim() === target;

  const save = async () => {
    if (!confirmed) return;
    const saved = await patch({ active: reactivate });
    if (saved) onSaved(saved);
  };

  return (
    <Modal
      open
      title={reactivate ? "Reactivate user" : "Deactivate user"}
      okText={reactivate ? "Reactivate" : "Deactivate"}
      okButtonProps={{ danger: !reactivate, disabled: !confirmed }}
      confirmLoading={pending}
      onOk={save}
      onCancel={onClose}
      destroyOnHidden
    >
      <PatchError error={error} onReload={onReload} />
      {reactivate ? (
        <Typography.Paragraph>
          <Typography.Text strong ellipsis={{ tooltip: userLabel(user) }} style={{ maxWidth: 360 }}>
            {userLabel(user)}
          </Typography.Text>{" "}
          can sign in again with their existing roles.
        </Typography.Paragraph>
      ) : (
        <>
          <Typography.Paragraph>
            <Typography.Text
              strong
              ellipsis={{ tooltip: userLabel(user) }}
              style={{ maxWidth: 360 }}
            >
              {userLabel(user)}
            </Typography.Text>{" "}
            is signed out everywhere right away and can no longer sign in. Their project assignments
            are kept.
          </Typography.Paragraph>
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            Type {user.email ? "their email" : "their name"} to confirm:
          </Typography.Paragraph>
          <Input
            aria-label="Confirm deactivation"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onPressEnter={save}
            placeholder={target}
            autoFocus
          />
        </>
      )}
    </Modal>
  );
}
