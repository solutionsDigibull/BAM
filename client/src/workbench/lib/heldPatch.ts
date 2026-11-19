// The ISATVON patch a correction proposed and the FDE has to decide (#715, follows #499): the build holds a Sub-Skill in
// "needs your review" instead of certifying it when the patch changes what the Sub-Skill means. The server decides every
// state and every refusal (409, 412, 422); this file reads the patch and sends the FDE's decision with an Idempotency-Key.
// Accepting sends the Sub-Skill's content hash as If-Match. The rationale is the model's text and is shown as text.
import { useCustom } from '@refinedev/core'
import type { HeldPatch, HeldPatchOp, HeldPatchResponse, PatchDecided } from '@/contracts'
import { api } from '../api/adapter'
import { idempotencyKey } from '../http/http'

export { isForbidden } from './workflowCode'
export { problemText } from './workflowBuilds'

export type Decision = 'accept' | 'reject'
export const MAX_DECISION_REASON = 500
const MAX_VALUE_CHARS = 400

/** A patch value as text, cut so one huge value cannot flood the card; never markup. */
export function showValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  const text = typeof value === 'string' ? value : (JSON.stringify(value) ?? '')
  return text.length > MAX_VALUE_CHARS ? `${text.slice(0, MAX_VALUE_CHARS)}…` : text
}

/** The ETag accepting sends: the Sub-Skill's content hash, quoted. */
export const ifMatchOf = (held: Pick<HeldPatch, 'content_sha256'>): string => `"${held.content_sha256}"`

export const OP_LABEL: Record<HeldPatchOp['op'], string> = { add: 'Add', remove: 'Remove', replace: 'Replace', test: 'Check' }

/** `/isatvon_definition/gate/max` -> `gate / max`: the part of the definition the operation touches. */
export const pathLabel = (path: string): string => path.replace(/^\/isatvon_definition\/?/, '').split('/').filter(Boolean).join(' / ') || 'the whole definition'

/** Rejecting is recorded with its reason; accepting may leave it out. */
export const reasonProblem = (decision: Decision, reason: string): string | undefined =>
  reason.length > MAX_DECISION_REASON ? `Use at most ${MAX_DECISION_REASON} characters.`
    : decision === 'reject' && reason.trim() === '' ? 'Say why you reject it; the reason is recorded.' : undefined

/** One key per decision dialog, so a double click or a retry after a lost answer is one command. */
export const newDecisionKey = (): string => idempotencyKey()

export function useHeldPatch(workflowId: string | undefined, subId: string | undefined, enabled = true) {
  const { query } = useCustom<HeldPatchResponse>({
    url: 'held-patch', method: 'get', config: { query: { workflowId, subId } },
    queryOptions: { enabled: !!workflowId && !!subId && enabled, retry: false }, // the card has its own Retry
  })
  return { held: query.data?.data.held ?? null, loaded: !!query.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export const decideHeldPatch = (workflowId: string, subId: string, decision: Decision, reason: string | undefined, key: string, ifMatch?: string): Promise<PatchDecided> =>
  api.decideHeldPatch(workflowId, subId, decision, reason, key, ifMatch)
