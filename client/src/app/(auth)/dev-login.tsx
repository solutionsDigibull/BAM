"use client";

import { Alert, Button, Card, Flex, Typography } from "antd";

import { devSignIn, keycloakSignIn } from "./actions";

const ERRORS: Record<string, string> = {
  WrongPortal: "This account cannot use this portal. Sign in through the portal for your role.",
  NoRole: "This account has no SBAM role. Ask your Admin for access.",
  Configuration: "Sign-in is unavailable, try again later.", // Keycloak unreachable
};

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

// Keycloak sign-in when KEYCLOAK_ISSUER is set (#31); the dev role picker stays as a local fallback.
export function DevLogin({
  portal,
  enabled,
  keycloak = false,
  error,
  reference,
  callbackUrl,
}: {
  portal: keyof typeof CHOICES;
  enabled: boolean;
  keycloak?: boolean;
  error?: string;
  reference?: string;
  callbackUrl?: string;
}) {
  return (
    <Flex align="center" justify="center" style={{ minHeight: "100vh", background: "#f6f7fb" }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={1} style={{ marginTop: 0, fontSize: 24 }}>
          SBAM {portal === "admin" ? "Admin" : "FDE"} sign-in
        </Typography.Title>
        {error && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message={ERRORS[error] ?? "Sign-in failed. Try again."}
            description={reference ? `Reference: ${reference}` : undefined}
          />
        )}
        {keycloak && (
          <form action={keycloakSignIn} style={{ marginBottom: enabled ? 16 : 0 }}>
            <input type="hidden" name="portal" value={portal} />
            {callbackUrl && <input type="hidden" name="callbackUrl" value={callbackUrl} />}
            <Button type="primary" htmlType="submit" block>
              Sign in with Keycloak
            </Button>
          </form>
        )}
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
          !keycloak && (
            <Typography.Text type="secondary">
              Keycloak sign-in is not configured yet.
            </Typography.Text>
          )
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
