// Shared bits of the token caps screen (#776, ADR 0028 decision 6): the API resource of one project's caps, the five
// numbers the form edits and the copy. Shapes are the generated contracts; the API stays authoritative for validation.
import type { BudgetCaps } from "@/contracts";

// `GET /admin/projects/{id}/token-caps` read as record "token-caps" of "admin/projects/{id}", so the data provider
// keeps its ETag for the PUT.
export const ID = "token-caps";
export const resourceOf = (projectId: string) => `admin/projects/${projectId}`;
export const pathOf = (projectId: string) => `${resourceOf(projectId)}/${ID}`;
export const MAX_CAP = 1_000_000_000;

export type CapField = keyof BudgetCaps;
export type FieldSpec = { name: CapField; label: string; hint: string; max: number; min: number };

export const FIELDS: FieldSpec[] = [
  {
    name: "sub_skill_tokens",
    label: "One Sub-Skill build (tokens)",
    hint: "The most tokens a single Sub-Skill build may use. It cannot be above the Workflow cap.",
    min: 1,
    max: MAX_CAP,
  },
  {
    name: "workflow_tokens",
    label: "Workflow build (tokens)",
    hint: "The most tokens the builds of the project may use in all, apart from the knowledge build.",
    min: 1,
    max: MAX_CAP,
  },
  {
    name: "knowledge_tokens",
    label: "Knowledge build (tokens)",
    hint: "The most tokens the knowledge build (Cognee) may use.",
    min: 1,
    max: MAX_CAP,
  },
  {
    name: "first_alert_pct",
    label: "First alert at (%)",
    hint: "The FDE is told when a build has used this share of its cap.",
    min: 1,
    max: 98,
  },
  {
    name: "second_alert_pct",
    label: "Second alert at (%)",
    hint: "The FDE and the Admin are told; it must be above the first alert.",
    min: 2,
    max: 99,
  },
];

export const NOTE =
  "A build stops at 100% of its cap. The FDE or an Admin can raise a cap once per build, then press Retry. These limits apply to the chosen project only; a project without limits of its own uses the platform defaults.";
export const DEFAULTS_MESSAGE = "Using the platform defaults";
export const DEFAULTS_HELP =
  "This project has no limits of its own yet. Saving stores the numbers below for this project.";
export const STALE_MESSAGE = "These limits were changed by someone else";
