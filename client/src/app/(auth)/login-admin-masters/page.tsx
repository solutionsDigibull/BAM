import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { BullmLogin } from "../_login/bullm-login";
import { failureReference } from "../failure";

export const metadata = { title: "Admin Masters sign-in" };

/** Same card, Keycloak client and role as /login-admin (#869); it only lands in Admin Masters mode. */
const DEFAULT_CALLBACK = "/admin/masters";

export default async function AdminMastersLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  return (
    <BullmLogin
      portal="admin"
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
      reference={failureReference("admin", error)}
      callbackUrl={callbackUrl || DEFAULT_CALLBACK}
    />
  );
}
