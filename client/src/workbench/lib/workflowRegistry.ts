// The registry entry of a workflow, read only (#849): the registered name, the version, the image digest and manifest
// hash that were certified, the certification, and whether a later version superseded it (#760), from the SBAM API.
// Nothing here registers, enables or disables anything: registering is a service call of the Commit step (#766).
import { useCustom } from '@refinedev/core'
import type { RegisteredWorkflow } from '@/contracts'

export type Tone = 'ok' | 'warn' | 'err' | 'muted'

/** MAJOR.0.PATCH, the version form the lookup accepts. */
const VERSION = /^\d+\.0\.\d+$/
export const isValidVersion = (v: string): boolean => VERSION.test(v.trim())

const code = (e: unknown): number | undefined => (e as { statusCode?: number } | null | undefined)?.statusCode

/**
 * The lookup answers 404 for a workflow (or version) that has no entry, and also for another tenant. The workbench
 * is only reached from the user's own project, so a 404 here means "not registered", which is an empty state.
 */
export const isNotRegistered = (e: unknown): boolean => code(e) === 404
/** 403: the account may not see this project's workflow. */
export const isForbidden = (e: unknown): boolean => code(e) === 403

export const STATUS_LABEL: Record<RegisteredWorkflow['status'], string> = { REGISTERED: 'Current', SUPERSEDED: 'Superseded' }
export const statusTone = (s: RegisteredWorkflow['status']): Tone => (s === 'REGISTERED' ? 'ok' : 'muted')
export const isSuperseded = (r: Pick<RegisteredWorkflow, 'status'>): boolean => r.status === 'SUPERSEDED'

/** `sha256:` plus the first 12 hex digits; the whole value stays in the copy button. */
export const shortDigest = (d: string): string => `sha256:${d.replace(/^sha256:/, '').slice(0, 12)}`

/** How many tools of each role the server registers (every Sub-Skill has one action and one check tool). */
export function toolCounts(tools: RegisteredWorkflow['tools']): { action: number; check: number } {
  return { action: tools.filter(t => t.role === 'action').length, check: tools.filter(t => t.role === 'check').length }
}

export function useWorkflowRegistration(workflowId: string | undefined, version?: string) {
  const { query } = useCustom<RegisteredWorkflow>({
    url: 'workflow-registration', method: 'get', config: { query: { workflowId, version } },
    queryOptions: { enabled: !!workflowId, retry: false }, // the screen has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
