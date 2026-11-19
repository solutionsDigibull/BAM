// One knowledge-graph entity with the passages of the documents it was read from, read only (#712), through the Refine
// data provider. The backend answers 404 for a version or node that is not readable (also for another tenant) and 403
// for an account that is not assigned to the project.
import { useCustom } from '@refinedev/core'
import type { KnowledgeNodeDetail, SourcePassage } from '@/contracts'

const code = (e: unknown): number | undefined => (e as { statusCode?: number } | null | undefined)?.statusCode
export const isNodeForbidden = (e: unknown): boolean => code(e) === 403
export const isNodeGone = (e: unknown): boolean => code(e) === 404

/** Where in the document a passage sits: "page 4", "sheet Costs, cell B7", "line 12", or the whole document. */
export function locationLabel(l: SourcePassage['location']): string {
  const parts: string[] = []
  if (l.page != null) parts.push(`page ${l.page}`)
  if (l.sheet) parts.push(`sheet ${l.sheet}`)
  if (l.cell) parts.push(`cell ${l.cell}`)
  if (l.line != null) parts.push(`line ${l.line}`)
  return parts.length ? parts.join(', ') : 'whole document'
}

export function useKnowledgeNode(versionId: string | undefined, nodeId: string | undefined) {
  const { query } = useCustom<KnowledgeNodeDetail>({
    url: 'knowledge-node', method: 'get', config: { query: { versionId, nodeId } },
    queryOptions: { enabled: !!versionId && !!nodeId, retry: false }, // the panel has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
