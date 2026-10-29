// Manifest viewer (E9.6): API paths and the pure helpers the screen uses. Plain data and functions,
// so they are easy to test.
import type { ManifestView } from "@/contracts";

const W = (p: string, w: string) => `projects/${p}/workflows/${w}`;

export type Subject = "sub-skill" | "workflow";
export const NOT_GENERATED = "Not generated yet";

export const versionsUrl = (p: string, w: string, subject: Subject, sub: string | null) =>
  subject === "workflow" ? `${W(p, w)}/manifests` : `${W(p, w)}/sub-skills/${sub}/manifests`;
export const manifestUrl = (
  p: string,
  w: string,
  subject: Subject,
  sub: string | null,
  buildNo: number,
) =>
  subject === "workflow"
    ? `${W(p, w)}/manifest?build_no=${buildNo}`
    : `${W(p, w)}/sub-skills/${sub}/manifest?build_no=${buildNo}`;
export const schemaUrl = (subject: Subject) =>
  `schemas/manifest/${subject === "workflow" ? "workflow_manifest" : "skill_manifest"}`;
export const schemaSqlUrl = (p: string, w: string) => `${W(p, w)}/schema-sql`;
export const workbenchHref = (p: string, w: string) => `/projects/${p}/workflows/${w}/isatvon`;

/** The hash the manifest itself carries (governance.deterministic_hash / integrity.content_sha256),
 * which is what Copy hash copies; the row's sha256 when the manifest lacks it. */
export function hashOf(view: ManifestView): string {
  const m = view.manifest as Record<string, unknown>;
  const governance = m.governance as Record<string, unknown> | undefined;
  const integrity = m.integrity as Record<string, unknown> | undefined;
  const hash = governance?.deterministic_hash ?? integrity?.content_sha256;
  return typeof hash === "string" ? hash : view.sha256;
}

/** Pretty JSON with keys sorted at every level, so a diff of two versions shows only what changed. */
export function pretty(value: unknown): string {
  const sort = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(sort)
      : v && typeof v === "object"
        ? Object.fromEntries(
            Object.keys(v as object)
              .sort()
              .map((k) => [k, sort((v as Record<string, unknown>)[k])]),
          )
        : v;
  return JSON.stringify(sort(value), null, 2);
}
