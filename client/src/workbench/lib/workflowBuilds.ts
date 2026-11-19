// The builds of a workflow, read only, plus Retry (#715): one row per Sub-Skill with a status for every gate, the findings
// of one attempt's gates, and the FDE's Retry of a Sub-Skill that waits for them. The server decides every state and every
// refusal (409 NOT_IN_REVIEW); this file only reads, shows and sends. The gate list mirrors `sbam_contracts.build_dashboard`.
import { useCustom } from '@refinedev/core'
import type { BuildRow, BuildsResponse, GateName, GateStatus, GatesResponse, SubSkillState } from '@/contracts'
import { api } from '../api/adapter'
import { idempotencyKey } from '../http/http'

export { isForbidden } from './workflowCode'

/** `sbam_contracts.build.RetryAccepted`: the contract is not in the generated client types, so the shape is kept here. */
export interface RetryAccepted { sub_skill_id: string; build_no: number }

export type GateFamily = 'Contract' | 'AST' | 'Static' | 'Compliance' | 'Review' | 'Tests'
export type Tone = 'ok' | 'warn' | 'err' | 'muted'

/** The gate columns in the order of the contract, with the labels the build dashboard (TD §9.3) uses. */
export const GATES: readonly { name: GateName; label: string; family: GateFamily }[] = [
  { name: 'ISATVON_CONTRACT', label: 'Contract', family: 'Contract' },
  { name: 'LIBRARIES', label: 'Libraries', family: 'Contract' },
  { name: 'SCHEMA_SQL', label: 'Schema SQL', family: 'Contract' },
  { name: 'CODEGEN', label: 'Codegen', family: 'Contract' },
  { name: 'AST_SYNTAX', label: 'Syntax', family: 'AST' },
  { name: 'AST_SKELETON', label: 'Skeleton', family: 'AST' },
  { name: 'AST_CONFORMANCE', label: 'Conformance', family: 'AST' },
  { name: 'AST_DECOUPLING', label: 'Decoupling', family: 'AST' },
  { name: 'AST_NO_MANUAL_LOOP', label: 'No loop', family: 'AST' },
  { name: 'AST_HANDOFF', label: 'Handoff', family: 'AST' },
  { name: 'AST_IMPORTS', label: 'AST imports', family: 'AST' },
  { name: 'AST_SQL_SAFETY', label: 'SQL safety', family: 'AST' },
  { name: 'RUFF', label: 'Ruff', family: 'Static' },
  { name: 'BLACK', label: 'Black', family: 'Static' },
  { name: 'MYPY', label: 'MyPy', family: 'Static' },
  { name: 'CLAMAV', label: 'ClamAV', family: 'Static' },
  { name: 'BANDIT', label: 'Bandit', family: 'Static' },
  { name: 'IMPORT_RESOLUTION', label: 'Imports', family: 'Static' },
  { name: 'REPO_LICENCE', label: 'Licence', family: 'Compliance' },
  { name: 'DEPENDENCY_VULN', label: 'Vulnerabilities', family: 'Compliance' },
  { name: 'MCP_COMPLIANCE', label: 'MCP', family: 'Compliance' },
  { name: 'UI_BUNDLE', label: 'UI bundle', family: 'Compliance' },
  { name: 'REVIEW', label: 'Review', family: 'Review' },
  { name: 'LOOP_TESTS', label: 'Loop tests', family: 'Tests' },
]
export const GATE_LABEL: Record<string, string> = Object.fromEntries(GATES.map(g => [g.name, g.label]))
export const gateLabel = (name: string): string => GATE_LABEL[name] ?? name

export const GATE_STATUS_LABEL: Record<GateStatus, string> = { PENDING: 'Waiting', RUNNING: 'Running', PASSED: 'Passed', FAILED: 'Failed', SKIPPED: 'Skipped' }
export const gateTone = (s: GateStatus): Tone => (s === 'PASSED' ? 'ok' : s === 'FAILED' ? 'err' : s === 'RUNNING' ? 'warn' : 'muted')

/** A gate missing from the row has not been reached: it is waiting. */
export const gateStatus = (row: Pick<BuildRow, 'gates'>, gate: GateName): GateStatus => row.gates?.[gate] ?? 'PENDING'

/** The gates of a row that have a result worth showing (everything but "waiting"), in column order. */
export const reachedGates = (row: Pick<BuildRow, 'gates'>): { name: GateName; label: string; status: GateStatus }[] =>
  GATES.flatMap(g => { const status = gateStatus(row, g.name); return status === 'PENDING' ? [] : [{ name: g.name, label: g.label, status }] })

export const hasFailedGate = (row: Pick<BuildRow, 'gates'>): boolean => GATES.some(g => gateStatus(row, g.name) === 'FAILED')

const STATE_LABEL: Record<SubSkillState, string> = {
  DRAFT: 'Draft', VALID: 'Ready to build', INVALID: 'Invalid', BUILDING: 'Building', CORRECTING: 'Correcting', TESTING: 'Testing',
  TESTING_BLOCKED: 'Testing blocked', NEEDS_FDE_REVIEW: 'Needs your review', DEPENDENCY_FAILED: 'Dependency failed', BUILD_ERROR: 'Build error',
  CERTIFIED: 'Certified', FROZEN: 'Frozen', INVALIDATED: 'Invalidated', SUPERSEDED: 'Superseded',
}
/** The server's state in plain words ("NEEDS_FDE_REVIEW" becomes "Needs your review"). A state this build does not know is shown as it is. */
export const stateLabel = (s: string): string => STATE_LABEL[s as SubSkillState] ?? s
export const stateTone = (s: string): Tone =>
  s === 'NEEDS_FDE_REVIEW' || s === 'TESTING_BLOCKED' || s === 'CORRECTING' ? 'warn'
    : s === 'INVALID' || s === 'BUILD_ERROR' || s === 'DEPENDENCY_FAILED' ? 'err' : s === 'CERTIFIED' || s === 'FROZEN' ? 'ok' : 'muted'

/**
 * Retry is offered only to a Sub-Skill that waits for the FDE (TD §13.2); the server still answers 409. TESTING_BLOCKED
 * waits too: the sandbox was unreachable and Retry re-runs only the tests.
 */
export const canRetry = (state: string | undefined): boolean => state === 'NEEDS_FDE_REVIEW' || state === 'TESTING_BLOCKED'

export const MAX_REASON = 500

export const POLL_MS = 3_000
/** Poll the builds only while one is still building or testing (E12.6-T07); a settled workflow is not polled. */
export const pollMs = (b: BuildsResponse | undefined): number | false => (b?.active ? POLL_MS : false)

export function summary(rows: BuildRow[]): { total: number; failed: number; review: number } {
  return { total: rows.length, failed: rows.filter(hasFailedGate).length, review: rows.filter(r => canRetry(r.state)).length }
}

/** What the server said, in its own words (problem+json `detail`), for a refused request. */
export function problemText(error: unknown): string {
  const e = error as { problem?: { detail?: string; title?: string }; message?: string } | null | undefined
  return e?.problem?.detail ?? e?.problem?.title ?? e?.message ?? 'The request was not accepted.'
}

export function useBuilds(workflowId: string | undefined) {
  const { query } = useCustom<BuildsResponse>({
    url: 'workflow-builds', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId, retry: false, refetchInterval: q => pollMs(q.state.data?.data) }, // the panel has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useGates(workflowId: string | undefined, subId: string | undefined, attempt?: number) {
  const { query } = useCustom<GatesResponse>({
    url: 'sub-skill-gates', method: 'get', config: { query: { workflowId, subId, attempt: attempt === undefined ? undefined : String(attempt) } },
    queryOptions: { enabled: !!workflowId && !!subId, retry: false },
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

/** A fresh key per Retry dialog, so a double click is one command. */
export const newRetryKey = (): string => idempotencyKey()
export const sendRetry = (workflowId: string, subId: string, reason: string, key: string): Promise<RetryAccepted> =>
  api.retrySubSkill(workflowId, subId, reason, key)
