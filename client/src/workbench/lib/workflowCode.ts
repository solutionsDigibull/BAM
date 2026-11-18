// The whole workflow's stored code, read-only (#841): the tree and one file at a time from the merged #763 API,
// plus the pure helpers the Translate preview uses. The server decides every tag and every finding; nothing here
// writes or invents data.
import { useCustom } from '@refinedev/core'
import type { CodeFinding, FileTag, WorkflowCodeGroup, WorkflowCodeTree, WorkflowFileView } from '@/contracts'

export const TAGS: readonly FileTag[] = ['reused', 'customized', 'generated']
export const TAG_LABEL: Record<FileTag, string> = { reused: 'Reused', customized: 'Customized', generated: 'Generated' }
export const TAG_HINT: Record<FileTag, string> = {
  reused: 'Upstream repository code, stored untouched',
  customized: 'A patch file, or a repository file a patch changed',
  generated: 'Written by the SBAM generator',
}

/** How many files carry each tag across the whole tree. */
export function tagCounts(tree: WorkflowCodeTree): Record<FileTag, number> {
  const counts: Record<FileTag, number> = { reused: 0, customized: 0, generated: 0 }
  for (const g of tree.groups) for (const f of g.files) counts[f.tag] += 1
  return counts
}

export const fileCount = (tree: WorkflowCodeTree): number => tree.groups.reduce((n, g) => n + g.files.length, 0)

/** The first file of the first group that has one, the preview's default selection. */
export function firstFile(tree: WorkflowCodeTree): { group: string; path: string } | undefined {
  for (const g of tree.groups) if (g.files[0]) return { group: g.group_id, path: g.files[0].path }
  return undefined
}

export const fileKey = (group: string, path: string): string => `${group}\u0000${path}`

/** A gate or attempt status as a tone for its pill. The server's words are shown as they are. */
export function statusTone(status?: string | null): 'ok' | 'warn' | 'err' | 'muted' {
  if (!status) return 'muted'
  if (/pass|succe|ok|certif|done|clean/i.test(status)) return 'ok'
  if (/fail|error|block|refus|reject/i.test(status)) return 'err'
  if (/run|build|pend|queue|review|wait/i.test(status)) return 'warn'
  return 'muted'
}

export const groupHasProblem = (g: WorkflowCodeGroup): boolean =>
  g.findings.length > 0 || g.files.some(f => f.findings.length > 0) || g.gates.some(x => statusTone(x.status) === 'err')

/** The lines of a file as shown: a final newline does not add an empty last line. */
export function splitLines(content: string): string[] {
  const lines = content.split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

/** Findings by line number, for the markers in the gutter. A finding with no line, or a line past the end of the
 *  (possibly cut) file, cannot be placed and is returned in `unplaced` so it is still listed. */
export function placeFindings(findings: CodeFinding[], lineCount: number): { byLine: Map<number, CodeFinding[]>; unplaced: CodeFinding[] } {
  const byLine = new Map<number, CodeFinding[]>()
  const unplaced: CodeFinding[] = []
  for (const f of findings) {
    if (f.line && f.line >= 1 && f.line <= lineCount) byLine.set(f.line, [...(byLine.get(f.line) ?? []), f])
    else unplaced.push(f)
  }
  return { byLine, unplaced }
}

export const findingText = (f: CodeFinding): string => `${f.gate} ${f.code}: ${f.message}`

/** 403 and 404 both mean "not yours": another tenant and an unassigned FDE get 404 by design. */
export function isForbidden(error: unknown): boolean {
  const code = (error as { statusCode?: number } | null | undefined)?.statusCode
  return code === 403 || code === 404
}

export function useWorkflowCode(workflowId: string | undefined) {
  const { query } = useCustom<WorkflowCodeTree>({
    url: 'workflow-code', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId, retry: false }, // the screen has its own Retry
  })
  return { tree: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useWorkflowFile(workflowId: string | undefined, group: string | undefined, path: string | undefined) {
  const { query } = useCustom<WorkflowFileView>({
    url: 'workflow-file', method: 'get', config: { query: { workflowId, group, path } },
    queryOptions: { enabled: !!workflowId && !!group && !!path, retry: false },
  })
  return { file: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
