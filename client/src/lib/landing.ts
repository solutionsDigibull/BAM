// Where a user lands, and which callbackUrl values are allowed (E2.5). Pure functions: no I/O.
import type { Role } from "./roles";

export type Portal = "admin" | "fde";

const PREFIX: Record<Portal, string> = { admin: "/admin", fde: "/projects" };

/** The landing page for a user signing in through `portal`. */
export function landing(portal: Portal, roles: readonly Role[]): string {
  if (portal === "fde") return "/projects";
  // A platform operator without a tenant admin role manages tenants.
  return roles.includes("super-admin") && !roles.includes("sbam-admin")
    ? "/admin/tenants"
    : "/admin/projects";
}

/**
 * `raw` if it is a same-origin relative path inside the portal, else null (the caller falls back
 * to the landing page). Drops absolute and protocol-relative URLs, backslash tricks, and the other
 * portal's paths, so the callback can never redirect off-site (open redirect).
 */
export function safeCallback(raw: string | null | undefined, portal: Portal): string | null {
  // Backslashes, whitespace and control characters (URL parsing silently drops some of them).
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || /[\\\s\p{Cc}]/u.test(raw)) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(raw, "http://sbam.invalid");
  } catch {
    return null;
  }
  if (url.origin !== "http://sbam.invalid") return null;
  const prefix = PREFIX[portal];
  const inside = url.pathname === prefix || url.pathname.startsWith(`${prefix}/`);
  return inside ? `${url.pathname}${url.search}` : null;
}
