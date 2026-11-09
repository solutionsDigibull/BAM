import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { FdeLogin } from "../_login/fde-login";
import { failureReference } from "../failure";

export const metadata = { title: "FDE sign-in" };

export default async function FdeLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  return (
    <FdeLogin
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
      reference={failureReference("fde", error)}
      callbackUrl={callbackUrl}
    />
  );
}
