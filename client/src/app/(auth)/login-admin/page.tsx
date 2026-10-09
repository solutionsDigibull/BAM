import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";

export const metadata = { title: "Admin sign-in" };

export default async function AdminLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <DevLogin
      portal="admin"
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
    />
  );
}
