// Shared bits of the Admin Projects screens (E3.9): API paths, the problem codes they branch on and the copy.
import type { Project, ProjectCreate, ProjectMembers, TenantUser } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const RESOURCE = "admin/projects";
export const archivePath = (id: string) => `${RESOURCE}/${id}/archive`;
// GET/PUT members and the user list come with #50 (E2.4); shapes are the generated contracts.
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

export const memberIds = (m: ProjectMembers | undefined): string[] =>
  (m?.members ?? []).map((u) => u.id);

/** Only ACTIVE `sbam-fde` users can be assigned (the API answers 422 for anyone else). */
export const selectableFdes = (users: TenantUser[]): TenantUser[] =>
  users.filter((u) => u.portal_roles.includes("sbam-fde") && u.status === "ACTIVE");

export const userLabel = (u: TenantUser) => u.display_name ?? u.email ?? u.id;
