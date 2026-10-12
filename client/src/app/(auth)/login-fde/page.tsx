import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";
import { failureReference } from "../failure";

export const metadata = { title: "FDE sign-in" };

export default async function FdeLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  return (
    <DevLogin
      portal="fde"
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
      reference={failureReference("fde", error)}
      callbackUrl={callbackUrl}
    />
  );
}
