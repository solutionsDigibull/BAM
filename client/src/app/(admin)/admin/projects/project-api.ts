// Shared bits of the Admin Projects screens (E3.9): API paths, the problem codes they branch on and the copy.
import type { Project, ProjectCreate, UserProfile } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const RESOURCE = "admin/projects";
export const archivePath = (id: string) => `${RESOURCE}/${id}/archive`;
// GET/PUT members and the FDE list come with #50 (E2.4), coded against TD §8.2 / §8.3.
export const membersPath = (id: string) => `${RESOURCE}/${id}/members`;
export const USERS_PATH = "admin/users";
export const MAX_MEMBERS = 50;

export const LIMITS = { name: 120, client_name: 120, description: 2000, context_notes: 10000 };

export const EMPTY_MESSAGE = "Create your first project";
export const DUPLICATE_NAME_MESSAGE = "A project with this name already exists.";
export const PIPELINE_RUNNING_MESSAGE = "Processing is running; archive later";
export const ARCHIVED_MESSAGE = "This project is archived and read-only.";

export type ProjectForm = Pick<
  ProjectCreate,
  "name" | "client_name" | "description" | "context_notes"
>;
/** A row of GET /admin/users (#50). `active` is optional until #50 fixes the user contract. */
export type TenantUser = UserProfile & { active?: boolean };
/** GET/PUT members answer `{members}`: user objects, or bare ids. */
export type Members = { members: (string | { id: string })[] };

export const problemCode = (error: unknown): string | undefined =>
  (error as ApiError | null)?.problem?.code;

export const statusOf = (error: unknown): number | undefined =>
  (error as ApiError | null)?.statusCode;

/** Trim the form and send `null` for empty optional fields, so clearing one clears it on the server. */
export function toBody(values: ProjectForm): ProjectForm {
  const optional = (v: string | null | undefined) => (v?.trim() ? v : null);
  return {
    name: values.name.trim(),
    client_name: optional(values.client_name)?.trim() ?? null,
    description: optional(values.description),
    context_notes: optional(values.context_notes),
  };
}

export const toForm = (p: Project): ProjectForm => ({
  name: p.name,
  client_name: p.client_name ?? undefined,
  description: p.description ?? undefined,
  context_notes: p.context_notes ?? undefined,
});

export const memberIds = (m: Members | undefined): string[] =>
  (m?.members ?? []).map((x) => (typeof x === "string" ? x : x.id));

/** Only active FDEs of the caller's tenant (the API filters too; this is belt and braces). */
export const selectableFdes = (users: TenantUser[]): TenantUser[] =>
  users.filter((u) => u.portal_roles.includes("sbam-fde") && u.active !== false);

export const userLabel = (u: TenantUser) => u.display_name ?? u.email ?? u.id;
