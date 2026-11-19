// The code attempts of one Sub-Skill, read only (#715, E11.5): attempt 0 is the first generation and 1 and 2 are the
// corrections. The list says what each attempt produced (status, template, the correction's reason, its files and findings),
// one attempt's file is shown with its generated-body lines marked (every other line is the protected template), and a diff
// shows what a correction changed in a file. The server holds every rule and decides every 404; nothing here writes.
import { useCustom } from '@refinedev/core'
import type { CodeAttemptList, CodeAttemptSummary, CodeDiff, CodeFileView, LineRange } from '@/contracts'

export { isForbidden } from './workflowCode'
export { problemText } from './workflowBuilds'

export const MAX_ATTEMPT = 2 // sbam_contracts.build.MAX_CORRECTIONS: attempts 0, 1 and 2

/** What attempt `n` is, in the words an FDE uses. */
export const attemptLabel = (n: number): string => (n === 0 ? 'First generation' : `Correction ${n}`)

/** The attempts newest first, for the list (the server answers them in order 0..2). */
export const newestFirst = (items: CodeAttemptSummary[]): CodeAttemptSummary[] => [...items].sort((a, b) => b.attempt - a.attempt)

/** A line is inside a generated-body slot when a range of `slots` holds it; every other line is protected template code. */
export const inSlot = (n: number, slots: LineRange[]): boolean => slots.some(r => n >= r.start && n <= r.end)

/** The attempts a diff can compare against: every other attempt that stored files. */
export const comparable = (items: CodeAttemptSummary[], of: number): CodeAttemptSummary[] => items.filter(a => a.attempt !== of && a.files.length > 0)

/** The role of a unified-diff line, from its first characters. */
export type DiffKind = 'add' | 'del' | 'hunk' | 'meta' | 'context'
export function diffKind(line: string): DiffKind {
  if (line.startsWith('+++') || line.startsWith('---')) return 'meta'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return 'context'
}

export function useAttempts(workflowId: string | undefined, subId: string | undefined) {
  const { query } = useCustom<CodeAttemptList>({
    url: 'code-attempts', method: 'get', config: { query: { workflowId, subId } },
    queryOptions: { enabled: !!workflowId && !!subId, retry: false },
  })
  return { items: query.data?.data.items, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useAttemptFile(workflowId: string | undefined, subId: string | undefined, attempt: number | undefined, path: string | undefined) {
  const { query } = useCustom<CodeFileView>({
    url: 'code-attempt-file', method: 'get', config: { query: { workflowId, subId, attempt: String(attempt), path } },
    queryOptions: { enabled: !!workflowId && !!subId && attempt !== undefined && !!path, retry: false },
  })
  return { file: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useAttemptDiff(workflowId: string | undefined, subId: string | undefined, from: number | undefined, to: number | undefined, path: string | undefined) {
  const { query } = useCustom<CodeDiff>({
    url: 'code-attempt-diff', method: 'get', config: { query: { workflowId, subId, from: String(from), to: String(to), path } },
    queryOptions: { enabled: !!workflowId && !!subId && from !== undefined && to !== undefined && !!path, retry: false },
  })
  return { diff: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
