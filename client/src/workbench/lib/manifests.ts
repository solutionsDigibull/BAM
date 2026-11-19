// The manifests of a workflow and of each Sub-Skill, read only (#715, E9.6): the versions stored (newest first, one per
// hash) and one manifest as stored, shown as text with its keys sorted so two versions read alike. Saving and approving a
// manifest have no backend yet and stay hidden. The server decides every 404; nothing here writes.
import { useCustom } from '@refinedev/core'
import type { ManifestVersion, ManifestVersionList, ManifestView } from '@/contracts'

export { isForbidden } from './workflowCode'
export { problemText } from './workflowBuilds'

/** `undefined` is the workflow's own manifest; a Sub-Skill id is that Sub-Skill's. */
export type Subject = string | undefined

/** The hash the manifest itself carries (governance.deterministic_hash or integrity.content_sha256); the row's hash when it has none. */
export function hashOf(view: Pick<ManifestView, 'manifest' | 'sha256'>): string {
  const m = view.manifest as Record<string, unknown>
  const governance = m.governance as Record<string, unknown> | undefined
  const integrity = m.integrity as Record<string, unknown> | undefined
  const hash = governance?.deterministic_hash ?? integrity?.content_sha256
  return typeof hash === 'string' ? hash : view.sha256
}

/** Pretty JSON with keys sorted at every level, so a diff of two versions shows only what changed. Never markup. */
export function pretty(value: unknown): string {
  const sort = (v: unknown): unknown =>
    Array.isArray(v) ? v.map(sort)
      : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v as object).sort().map(k => [k, sort((v as Record<string, unknown>)[k])]))
        : v
  return JSON.stringify(sort(value), null, 2)
}

/** Where a version came from: the build, and for a Sub-Skill the correction cycle and attempt. */
export function originOf(v: Pick<ManifestVersion, 'build_no' | 'cycle' | 'attempt'>): string {
  const parts = [`build ${v.build_no}`]
  if (v.cycle !== null && v.cycle !== undefined) parts.push(`cycle ${v.cycle}`)
  if (v.attempt !== null && v.attempt !== undefined) parts.push(`attempt ${v.attempt}`)
  return parts.join(' · ')
}

export const versionKey = (v: Pick<ManifestVersion, 'sha256'>): string => v.sha256

export function useManifestVersions(workflowId: string | undefined, subject: Subject) {
  const { query } = useCustom<ManifestVersionList>({
    url: 'manifest-versions', method: 'get', config: { query: { workflowId, subId: subject } },
    queryOptions: { enabled: !!workflowId, retry: false },
  })
  return { items: query.data?.data.items, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useManifest(workflowId: string | undefined, subject: Subject, v: Pick<ManifestVersion, 'version' | 'build_no'> | undefined) {
  const { query } = useCustom<ManifestView>({
    url: 'manifest', method: 'get', config: { query: { workflowId, subId: subject, version: v?.version, buildNo: v ? String(v.build_no) : undefined } },
    queryOptions: { enabled: !!workflowId && !!v, retry: false },
  })
  return { view: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
