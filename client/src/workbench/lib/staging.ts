// Staging mode (#861, ADR 0032, ADR 0036): what the Staging screen reads and the words it uses. The server decides
// everything (who may see Staging, what runs, the health); these helpers only name it. `GET W/staging` answers 404
// for a workflow that was never committed, for another tenant and for an unassigned project: one "not available" state.
import { useCustom } from '@refinedev/core'
import { guardPoll } from '@/lib/session-ended'
import type { BuildsResponse, ChangeNoteList, StagingHealth, StagingState, StagingStatus } from '@/contracts'

export type Tone = 'ok' | 'warn' | 'err' | 'muted'

export const STAGING_STATE_LABEL: Record<StagingState, string> = {
  NOT_DEPLOYED: 'Not deployed',
  PENDING_RUNTIME: 'Waiting for the Staging runtime',
  RUNNING: 'Running',
  FAILED: 'Failed to start',
  STOPPED: 'Stopped',
  SUPERSEDED: 'Superseded',
}
export const stagingStateTone = (s: StagingState): Tone =>
  s === 'RUNNING' ? 'ok' : s === 'FAILED' ? 'err' : s === 'PENDING_RUNTIME' ? 'warn' : 'muted'

export const HEALTH_LABEL: Record<StagingHealth, string> = { HEALTHY: 'Healthy', UNREACHABLE: 'Unreachable', UNKNOWN: 'Not checked' }
export const healthTone = (h: StagingHealth): Tone => (h === 'HEALTHY' ? 'ok' : h === 'UNREACHABLE' ? 'err' : 'muted')

/** The states that cannot change without the FDE doing something (Commit again, a new version): the screen stops polling. */
export const isTerminal = (s: StagingState | undefined): boolean => s === 'FAILED' || s === 'STOPPED' || s === 'SUPERSEDED'

/** How often the status is read: quickly while the runtime has not answered, slowly for the health of a running one, never when terminal. */
export const STAGING_WAIT_POLL_MS = 4000
export const STAGING_RUN_POLL_MS = 20000
export function stagingPollMs(s: StagingStatus | undefined): number | false {
  if (!s) return false
  if (s.state === 'PENDING_RUNTIME') return STAGING_WAIT_POLL_MS
  if (s.state === 'RUNNING') return STAGING_RUN_POLL_MS
  return false
}

/** The workflow's UI may be shown only for a running deployment with a UI address; everything else gets a status instead. */
export const canShowUi = (s: StagingStatus | undefined): boolean => s?.state === 'RUNNING' && !!s.ui_url

const code = (e: unknown): number | undefined => (e as { statusCode?: number } | null | undefined)?.statusCode
export const isForbidden = (e: unknown): boolean => code(e) === 403
/** 404: never committed, or not this user's project (the server does not say which). */
export const isNotAvailable = (e: unknown): boolean => code(e) === 404
export const isConflict = (e: unknown): boolean => code(e) === 409
export const problemOf = (e: unknown, fallback = 'The request failed.'): string =>
  (e as { problem?: { detail?: string } }).problem?.detail ?? (e as { message?: string }).message ?? fallback

export const shortDigest = (d: string | null | undefined): string => (d ? `sha256:${d.replace(/^sha256:/, '').slice(0, 12)}` : '—')

export function useStagingStatus(workflowId: string | undefined, enabled = true) {
  const { query } = useCustom<StagingStatus>({
    url: 'staging-status', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId && enabled, retry: false, refetchInterval: guardPoll(q => stagingPollMs(q.state.data?.data as StagingStatus | undefined)) },
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useChangeNotes(workflowId: string | undefined, enabled = true) {
  const { query } = useCustom<ChangeNoteList>({
    url: 'change-notes', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId && enabled, retry: false },
  })
  return { data: query.data?.data, error: query.error, refetch: query.refetch }
}

/** The Sub-Skills a change note can name (one row per Sub-Skill of the workflow's build). */
export function useSubSkillChoices(workflowId: string | undefined, enabled = true) {
  const { query } = useCustom<BuildsResponse>({
    url: 'workflow-builds', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId && enabled, retry: false },
  })
  return query.data?.data.sub_skills ?? []
}

export const MAX_NOTE_CHARS = 2000
