export const ROLES = ["super-admin", "sbam-admin", "sbam-fde"] as const;
export type Role = (typeof ROLES)[number];

export function parseRoles(raw: string): Role[] {
  return raw
    .split(",")
    .map((r) => r.trim())
    .filter((r): r is Role => (ROLES as readonly string[]).includes(r));
}

export const isAdmin = (roles: readonly Role[]) =>
  roles.includes("sbam-admin") || roles.includes("super-admin");

export const isFde = (roles: readonly Role[]) => roles.includes("sbam-fde");

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (value: string) => UUID.test(value);
