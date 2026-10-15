"use client";

import { useCustomMutation } from "@refinedev/core";
import { Checkbox, Form, Input, Modal } from "antd";
import { useState } from "react";

import { ApiErrorState } from "@/components/ui-kit";
import type { TenantUser, UserInvite } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

import { INVITE, ROLES, USER_EXISTS_MESSAGE, isEmail, problemCode, statusOf } from "./user-api";

/** Invite by email. Submit stays disabled until the form is valid, so a bad email is never sent. */
export function InviteDialog({
  onClose,
  onInvited,
}: {
  onClose: () => void;
  onInvited: (user: TenantUser) => void;
}) {
  const [form] = Form.useForm<UserInvite>();
  const email = Form.useWatch("email", form) ?? "";
  const roles = Form.useWatch("roles", form) ?? [];
  const [error, setError] = useState<ApiError | null>(null);
  const { mutateAsync, mutation } = useCustomMutation<TenantUser, ApiError>();
  const valid = isEmail(email) && roles.length > 0;

  const invite = async () => {
    if (!valid) return;
    setError(null);
    try {
      const res = await mutateAsync({
        url: INVITE,
        method: "post",
        values: { email: email.trim(), roles },
      });
      onInvited(res.data);
    } catch (e) {
      const err = e as ApiError;
      if (problemCode(err) === "USER_EXISTS")
        return form.setFields([{ name: "email", errors: [USER_EXISTS_MESSAGE] }]);
      const fields = (["email", "roles"] as const)
        .filter((name) => err.errors?.[name])
        .map((name) => ({ name, errors: [err.errors?.[name]].flat().map(String) }));
      if (statusOf(err) === 422 && fields.length) return form.setFields(fields);
      setError(err);
    }
  };

  return (
    <Modal
      open
      title="Invite user"
      okText="Send invite"
      okButtonProps={{ disabled: !valid }}
      confirmLoading={mutation.isPending}
      onOk={invite}
      onCancel={onClose}
      destroyOnHidden
    >
      {error && (
        <div style={{ marginBottom: 16 }}>
          <ApiErrorState error={error} onRetry={invite} />
        </div>
      )}
      <Form
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ roles: ["sbam-fde"] }}
        onFinish={invite}
      >
        <Form.Item
          name="email"
          label="Email"
          validateTrigger={["onChange", "onBlur"]}
          rules={[
            { required: true, message: "Enter an email address." },
            {
              validator: (_, v: string) =>
                !v || isEmail(v)
                  ? Promise.resolve()
                  : Promise.reject(new Error("Enter a valid email address.")),
            },
          ]}
          extra="They get an email to set a password and verify the address."
        >
          <Input autoFocus maxLength={254} placeholder="name@company.com" autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="roles"
          label="Roles"
          rules={[{ required: true, message: "Pick at least one role." }]}
        >
          <Checkbox.Group options={ROLES.map((r) => ({ value: r.value, label: r.label }))} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
