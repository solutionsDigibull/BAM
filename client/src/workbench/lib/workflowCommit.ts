// Commit to Staging (#766, ADR 0032, ADR 0034): what the Validation screen reads and the rules it draws. The server decides
// everything (the checks, the secrets gate, the stale hash); these helpers only name it. `manifest_hash` is the ETag a
// Commit sends back as If-Match, so a hash that changed since the FDE looked is refused with 409.
import { useCustom } from '@refinedev/core'
import type { CommitStatus, StagingStartResult } from '@/contracts'

export type Tone = 'ok' | 'warn' | 'err' | 'muted'

export const CHECK_LABEL: Record<string, string> = {
  WORKFLOW_CERTIFIED: 'Workflow certified',
  SUB_SKILLS_CERTIFIED: 'Every Sub-Skill certified on its current code',
  MCP_SERVER: 'Server image built',
  UI_BUNDLE: 'UI bundle checked',
  SECRETS: 'Secrets set and tested',
}
export const checkLabel = (name: string): string => CHECK_LABEL[name] ?? name

export const STATE_LABEL: Record<CommitStatus['state'], string> = {
  NOT_COMMITTED: 'Not committed',
  COMMITTING: 'Committing',
  COMMITTED: 'Committed to Staging',
}
export const stateTone = (s: CommitStatus['state']): Tone => (s === 'COMMITTED' ? 'ok' : s === 'COMMITTING' ? 'warn' : 'muted')

export const STAGING_LABEL: Record<StagingStartResult['status'], string> = {
  STARTED: 'Running in Staging',
  PENDING_RUNTIME: 'Waiting for the Staging runtime',
  FAILED: 'Staging could not start it',
}
export const stagingTone = (s: StagingStartResult['status']): Tone => (s === 'STARTED' ? 'ok' : s === 'FAILED' ? 'err' : 'warn')

const code = (e: unknown): number | undefined => (e as { statusCode?: number } | null | undefined)?.statusCode
export const isForbidden = (e: unknown): boolean => code(e) === 403
export const isNotFound = (e: unknown): boolean => code(e) === 404
export const problemOf = (e: unknown, fallback = 'The request failed.'): string =>
  (e as { problem?: { detail?: string } }).problem?.detail ?? (e as { message?: string }).message ?? fallback

/** A Commit run is going right now (the button waits for it). */
export const isRunning = (s: CommitStatus | undefined): boolean => s?.run?.state === 'RUNNING'

/** Why the Commit button is locked, or undefined when it may be pressed. The server checks again. */
export function commitReason(s: CommitStatus | undefined): string | undefined {
  if (!s) return 'Loading the checks'
  if (s.state === 'COMMITTED') return 'This version is already committed to Staging'
  if (isRunning(s)) return 'A commit is running'
  if (!s.manifest_hash) return 'There is no built workflow to commit yet'
  const failed = (s.checks ?? []).find(c => !c.passed)
  if (failed) return `${checkLabel(failed.name)}: ${failed.reason ?? 'not passed'}`
  return undefined
}

/** The status, refreshed every few seconds while a commit is going. */
export function useCommitStatus(workflowId: string | undefined) {
  const { query } = useCustom<CommitStatus>({
    url: 'commit-status', method: 'get', config: { query: { workflowId } },
    queryOptions: {
      enabled: !!workflowId, retry: false,
      refetchInterval: q => (q.state.data?.data?.state === 'COMMITTING' && isRunning(q.state.data.data) ? 3000 : false),
    },
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
