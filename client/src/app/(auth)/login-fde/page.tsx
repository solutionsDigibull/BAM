import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { BullmLogin } from "../_login/bullm-login";
import { failureReference } from "../failure";

export const metadata = { title: "FDE sign-in" };

export default async function FdeLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  return (
    <BullmLogin
      portal="fde"
      enabled={devAuthEnabled()}
      keycloak={keycloakEnabled()}
      error={error}
      reference={failureReference("fde", error)}
      callbackUrl={callbackUrl}
    />
  );
}
