// Shared bits of the Users & Roles screen (E2.6): API paths, the problem codes it branches on, and the copy.
// The API is E2.4 (#50); shapes are the generated contracts.
import type { TenantUser, UserPatch } from "@/contracts";

export { problemCode, statusOf } from "../projects/project-api";

export const USERS = "admin/users";
export const INVITE = `${USERS}/invite`;
export const userPath = (id: string) => `${USERS}/${id}`;

/** Every PATCH carries the row's version, so a change made meanwhile answers 412. */
export const ifMatch = (u: TenantUser) => ({ "if-match": `"${u.row_version}"` });
export type PatchBody = UserPatch;

// super-admin is never offered (the API answers 422 anyway).
export const ROLES = [
  { value: "sbam-admin", label: "Admin" },
  { value: "sbam-fde", label: "FDE" },
] as const;
export const roleLabel = (role: string) => ROLES.find((r) => r.value === role)?.label ?? role;

export const PAGE_SIZE = 100;
export const EMPTY_MESSAGE = "Invite your first FDE";
export const LAST_ADMIN_MESSAGE = "At least one active admin is required";
export const STALE_USER_MESSAGE = "This user was changed by someone else";
export const USER_EXISTS_MESSAGE = "A user with this email already exists.";

// Mirrors the API's `Email` contract (RFC 5322 via email-validator, E2.8) for instant feedback; the API
// stays authoritative and its 422 lands on the field. Unquoted local part (dot-atom), at most 64
// characters; a domain of dot-separated labels with a dot, no leading/trailing hyphen; 254 in total.
const LOCAL = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
const LABEL = /^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
export function isEmail(value: string): boolean {
  const email = value.trim();
  const at = email.lastIndexOf("@");
  if (email.length > 254 || at < 1) return false;
  const local = email.slice(0, at);
  const labels = email.slice(at + 1).split(".");
  return (
    local.length <= 64 &&
    LOCAL.test(local) &&
    labels.length >= 2 &&
    labels.every((label) => LABEL.test(label))
  );
}

export const isActiveAdmin = (u: TenantUser) =>
  u.status === "ACTIVE" && u.portal_roles.includes("sbam-admin");

export const userLabel = (u: TenantUser) => u.display_name || u.email || u.id;
