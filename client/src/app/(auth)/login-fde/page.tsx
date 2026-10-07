import { devAuthEnabled, keycloakEnabled } from "@/lib/session";

import { DevLogin } from "../dev-login";

export default async function FdeLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <DevLogin portal="fde" enabled={devAuthEnabled()} keycloak={keycloakEnabled()} error={error} />
  );
}
