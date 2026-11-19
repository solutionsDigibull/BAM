// The tests of one Sub-Skill, read only (#715, E13.4 / E13.6 / E13.7): the test data (synthetic cases), the Promptfoo runs
// of the sandbox with each assertion's result, the harness trace of every case of a run, and the sandbox log of a run as a
// download. Editing a case has a backend but is not part of this screen; certified evidence is never edited. The server
// decides every 404; nothing here writes.
import { useCustom } from '@refinedev/core'
import type { TestRunOut, TestRunState, TestRunTraces, TestRunsResponse, TestsResponse } from '@/contracts'
import { API_BASE } from '@/providers/data-provider'

export { isForbidden } from './workflowCode'
export { problemText } from './workflowBuilds'

export type Tone = 'ok' | 'warn' | 'err' | 'muted'

export const RUN_LABEL: Record<TestRunState, string> = { PASS: 'Passed', FAIL: 'Failed', NON_CERTIFYING: 'Not certifying', BLOCKED: 'Blocked' }
export const runTone = (s: TestRunState): Tone => (s === 'PASS' ? 'ok' : s === 'FAIL' ? 'err' : s === 'BLOCKED' ? 'warn' : 'muted')

export const KIND_LABEL: Record<string, string> = {
  PASS_FIRST: 'Passes first time', PASS_AFTER_RETRY: 'Passes after a retry', ESCALATION: 'Escalates', NO_OP: 'Nothing to do', EDGE: 'Edge case', FDE: 'Added by you',
}
export const kindLabel = (k: string): string => KIND_LABEL[k] ?? k

/** `3 of 5 passed`, or "not run" when a run carries no assertion. */
export const passText = (r: Pick<TestRunOut, 'passed' | 'total'>): string => (r.total === 0 ? 'No assertions' : `${r.passed} of ${r.total} passed`)

/** The assertions of a run grouped by case, in the order the run reported them. */
export function resultsByCase(run: Pick<TestRunOut, 'results'>): { name: string; passed: boolean; results: TestRunOut['results'] }[] {
  const out = new Map<string, TestRunOut['results']>()
  for (const r of run.results) out.set(r.case, [...(out.get(r.case) ?? []), r])
  return [...out].map(([name, results]) => ({ name, passed: results.every(r => r.passed), results }))
}

/** A value as indented JSON text; the panel collapses what is long. Never markup. */
export const jsonText = (value: unknown): string => (typeof value === 'string' ? value : (JSON.stringify(value, null, 2) ?? ''))

/** The sandbox log of a run, a short-lived signed download behind the API's redirect (served as an attachment, never rendered). */
export const sandboxLogsUrl = (projectId: string, workflowId: string, runId: string): string =>
  `${API_BASE}/projects/${projectId}/workflows/${workflowId}/sandbox-runs/${runId}/logs`

export function useSubSkillTests(workflowId: string | undefined, subId: string | undefined) {
  const { query } = useCustom<TestsResponse>({
    url: 'sub-skill-tests', method: 'get', config: { query: { workflowId, subId } },
    queryOptions: { enabled: !!workflowId && !!subId, retry: false },
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useTestRuns(workflowId: string | undefined, subId: string | undefined) {
  const { query } = useCustom<TestRunsResponse>({
    url: 'test-runs', method: 'get', config: { query: { workflowId, subId } },
    queryOptions: { enabled: !!workflowId && !!subId, retry: false },
  })
  return { runs: query.data?.data.runs, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useTestRunTraces(workflowId: string | undefined, subId: string | undefined, runId: string | undefined) {
  const { query } = useCustom<TestRunTraces>({
    url: 'test-run-traces', method: 'get', config: { query: { workflowId, subId, runId } },
    queryOptions: { enabled: !!workflowId && !!subId && !!runId, retry: false },
  })
  return { traces: query.data?.data.traces, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
