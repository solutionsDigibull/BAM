// The build timeline of a workflow (#926), read only: each Sub-Skill's newest build as ordered steps with tokens and cost,
// and the reason a parked build is parked. The server derives everything from rows the build wrote and never sends a
// message, a model output or a document content, so nothing here has to hide anything. Shapes: `sbam_contracts.build_timeline`.
import { useCustom } from '@refinedev/core'
import type { BuildTimelineResponse, StepKind, SubSkillTimeline, TimelineStep } from '@/contracts'

export { isForbidden } from './workflowCode'
export { problemText, stateLabel, stateTone } from './workflowBuilds'

export const TIMELINE_POLL_MS = 5_000
/** A build that is building, correcting or testing is polled; a parked or finished one is not. */
export const isActive = (state: string): boolean => state === 'BUILDING' || state === 'CORRECTING' || state === 'TESTING'
export const pollTimeline = (t: BuildTimelineResponse | undefined): number | false =>
  t?.sub_skills.some(s => isActive(s.state)) ? TIMELINE_POLL_MS : false

export const KIND_LABEL: Record<StepKind, string> = { TRANSITION: 'State', MODEL: 'Model', CODE_ATTEMPT: 'Code attempt', GATE: 'Gate' }

/** "1 min 5 s", "42 s", "250 ms"; null (the newest step) shows as an em dash. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ${s % 60} s`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

/** The server's money string, kept as it is, with the currency. */
export const formatCost = (usd: string): string => `$${usd}`
export const formatTokens = (n: number): string => n.toLocaleString('en-US')

/** One line that says what a step is: "generate bodies (sbam-codegen, 2 calls)". */
export function stepTitle(step: TimelineStep): string {
  const name = step.name.replace(/_/g, ' ')
  if (step.kind === 'MODEL') return `${name}${step.alias ? ` (${step.alias}, ${step.calls} call${step.calls === 1 ? '' : 's'})` : ''}`
  if (step.kind === 'TRANSITION') return `${name}${step.from_state && step.to_state ? `: ${step.from_state} to ${step.to_state}` : ''}`
  if (step.kind === 'GATE') return `${name} failed`
  return name
}

/** How long an active build has been quiet; null for a build that is not active. A large value means it may be stuck. */
export const idleMs = (sub: SubSkillTimeline, now: number): number | null =>
  sub.last_activity_at && isActive(sub.state) ? Math.max(0, now - Date.parse(sub.last_activity_at)) : null

export function useBuildTimeline(workflowId: string | undefined) {
  const { query } = useCustom<BuildTimelineResponse>({
    url: 'build-timeline', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId, retry: false, refetchInterval: q => pollTimeline(q.state.data?.data) },
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
