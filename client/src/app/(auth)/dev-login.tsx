"use client";

import { Button, Card, Flex, Typography } from "antd";

import { devSignIn } from "./actions";

const CHOICES = {
  admin: [
    { roles: "sbam-admin", label: "Sign in as Admin" },
    { roles: "super-admin", label: "Sign in as Super-admin" },
  ],
  fde: [
    { roles: "sbam-fde", label: "Sign in as FDE" },
    { roles: "sbam-admin,sbam-fde", label: "Sign in as Admin + FDE" },
  ],
};

// ponytail: dev role picker until #51 replaces this with the Auth.js Keycloak sign-in.
export function DevLogin({ portal, enabled }: { portal: keyof typeof CHOICES; enabled: boolean }) {
  return (
    <Flex align="center" justify="center" style={{ minHeight: "100vh", background: "#f6f7fb" }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          SBAM {portal === "admin" ? "Admin" : "FDE"} sign-in
        </Typography.Title>
        {enabled ? (
          <Flex vertical gap={8}>
            <Typography.Text type="secondary">
              Development sign-in (SBAM_DEV_AUTH=1)
            </Typography.Text>
            {CHOICES[portal].map((c) => (
              <form key={c.roles} action={devSignIn}>
                <input type="hidden" name="roles" value={c.roles} />
                <Button type="primary" htmlType="submit" block>
                  {c.label}
                </Button>
              </form>
            ))}
          </Flex>
        ) : (
          <Typography.Text type="secondary">
            Keycloak sign-in is not configured yet.
          </Typography.Text>
        )}
        <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          <a href={portal === "admin" ? "/login-fde" : "/login-admin"}>
            {portal === "admin" ? "FDE? Use the FDE login" : "Admin? Use the Admin login"}
          </a>
        </Typography.Paragraph>
      </Card>
    </Flex>
  );
}
