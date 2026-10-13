import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";
import { failureReference } from "../failure";

export const metadata = { title: "Admin sign-in" };

export default async function AdminLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  return (
    <DevLogin
      portal="admin"
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
      reference={failureReference("admin", error)}
      callbackUrl={callbackUrl}
    />
  );
}
