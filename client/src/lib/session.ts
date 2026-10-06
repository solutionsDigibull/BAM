import "server-only";

import { cookies } from "next/headers";

import { parseRoles, type Role } from "./roles";

export type Session = { roles: Role[] };

export const DEV_ROLES_COOKIE = "sbam_dev_roles";

/** Dev sign-in is off unless SBAM_DEV_AUTH=1, and never available in a production build. */
export function devAuthEnabled(): boolean {
  return process.env.SBAM_DEV_AUTH === "1" && process.env.NODE_ENV !== "production";
}

// ponytail: dev-cookie stand-in until #51 (E2.5) swaps in the Auth.js Keycloak session.
// Keep this signature: the (admin)/(fde) layout guards call it.
export async function getSession(): Promise<Session | null> {
  // Read the request first so guarded routes are always rendered per request, never prerendered.
  const jar = await cookies();
  if (!devAuthEnabled()) return null;
  const roles = parseRoles(jar.get(DEV_ROLES_COOKIE)?.value ?? "");
  return roles.length > 0 ? { roles } : null;
}
