import "server-only";

import { getToken, type JWT } from "next-auth/jwt";
import { cookies, headers } from "next/headers";

import type { Portal } from "./landing";
import { parseRoles, type Role } from "./roles";

export type Session = { roles: Role[] };

export const DEV_ROLES_COOKIE = "sbam_dev_roles";

/** Dev sign-in is off unless SBAM_DEV_AUTH=1, and never available in a production build. */
export function devAuthEnabled(): boolean {
  return process.env.SBAM_DEV_AUTH === "1" && process.env.NODE_ENV !== "production";
}

/** Keycloak sign-in (Auth.js) is on when KEYCLOAK_ISSUER and AUTH_SECRET are set. */
export function keycloakEnabled(): boolean {
  return Boolean(process.env.KEYCLOAK_ISSUER && process.env.AUTH_SECRET);
}

/**
 * The decoded Auth.js JWT, without running any callback. Rendering and the route guard only need
 * the roles; refreshing here would reuse a rotated refresh token, because these contexts can't
 * write the new cookie. The refresh happens in /api/proxy, which can (E2.5).
 */
export async function sessionToken(): Promise<JWT | null> {
  if (!keycloakEnabled()) return null;
  return getToken({
    req: { headers: await headers() },
    secret: process.env.AUTH_SECRET,
    secureCookie: process.env.AUTH_URL?.startsWith("https:") ?? false,
  });
}

// Keep this signature: the (admin)/(fde) layout guards call it. Keycloak session first; the dev
// cookie is the fallback for local work without Keycloak.
export async function getSession(): Promise<Session | null> {
  // Read the request first so guarded routes are always rendered per request, never prerendered.
  const jar = await cookies();
  const token = await sessionToken();
  if (token && !token.error) {
    const roles = parseRoles(((token.roles as string[] | undefined) ?? []).join(","));
    if (roles.length) return { roles };
  }
  if (!devAuthEnabled()) return null;
  const roles = parseRoles(jar.get(DEV_ROLES_COOKIE)?.value ?? "");
  return roles.length > 0 ? { roles } : null;
}

/** Portal and id token of the Keycloak session, for RP-initiated logout. */
export async function logoutHint(): Promise<{ portal?: Portal; idToken?: string }> {
  const token = await sessionToken();
  return {
    portal: token?.portal as Portal | undefined,
    idToken: token?.idToken as string | undefined,
  };
}
