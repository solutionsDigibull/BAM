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

// Same shape check as the API's `Email` contract (RFC 5322 in full is follow-up #212).
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const isEmail = (value: string) => value.length <= 254 && EMAIL.test(value.trim());

export const isActiveAdmin = (u: TenantUser) =>
  u.status === "ACTIVE" && u.portal_roles.includes("sbam-admin");

export const userLabel = (u: TenantUser) => u.display_name || u.email || u.id;
