import "server-only";

import { cookies } from "next/headers";

import { auth } from "@/auth";

import { parseRoles, type Role } from "./roles";

export type Session = { roles: Role[] };

export const DEV_ROLES_COOKIE = "sbam_dev_roles";

/** Dev sign-in is off unless SBAM_DEV_AUTH=1, and never available in a production build. */
export function devAuthEnabled(): boolean {
  return process.env.SBAM_DEV_AUTH === "1" && process.env.NODE_ENV !== "production";
}

/** Keycloak sign-in (Auth.js) is on when KEYCLOAK_ISSUER is set. */
export function keycloakEnabled(): boolean {
  return Boolean(process.env.KEYCLOAK_ISSUER);
}

// Keep this signature: the (admin)/(fde) layout guards call it. Keycloak session first; the dev
// cookie is the fallback for local work without Keycloak.
export async function getSession(): Promise<Session | null> {
  // Read the request first so guarded routes are always rendered per request, never prerendered.
  const jar = await cookies();
  if (keycloakEnabled()) {
    const session = await auth();
    if (session?.roles?.length) return { roles: session.roles };
  }
  if (!devAuthEnabled()) return null;
  const roles = parseRoles(jar.get(DEV_ROLES_COOKIE)?.value ?? "");
  return roles.length > 0 ? { roles } : null;
}
