// The Upload table's Confidence cell (#802): how useful a file is for the project's goal, as the backend's model judged
// it from the file's content (`DocumentOut.relevance`). Nothing here guesses: before the backend has an answer the cell
// says why there is none (still reading, being scored, or no goal to score against).
import type { DocumentRelevance, RelevanceLevel } from '@/contracts'

export const LEVEL_LABEL: Record<RelevanceLevel, string> = { HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low' }

export type RelevanceCell =
  | { kind: 'score'; level: RelevanceLevel; label: string; score: number; reason: string }
  | { kind: 'note'; label: string; title: string }

export interface RelevanceRow {
  /** The Upload row's state: only a `ready` file (passed its checks) can be scored. */
  state: string
  /** True once the backend has read the file (PARSED). */
  parsed?: boolean
  relevance?: DocumentRelevance
}

/** What the cell shows for a row: every step until the score arrives, then the score; null for a failed row (it is
 *  listed under Needs attention with its reason, and the cell shows a dash). */
export function relevanceCell(row: RelevanceRow, hasGoal: boolean): RelevanceCell | null {
  if (row.state === 'failed') return null
  if (row.state === 'uploading') return { kind: 'note', label: 'Uploading…', title: 'The file is being uploaded.' }
  if (row.state !== 'ready') return { kind: 'note', label: 'Checking…', title: 'The virus scan and the type check are running.' }
  const r = row.relevance
  if (r) return { kind: 'score', level: r.level, label: LEVEL_LABEL[r.level], score: r.score, reason: r.reason }
  if (!hasGoal) return { kind: 'note', label: 'Set a goal', title: 'Set the project goal to score how useful each file is for it.' }
  if (!row.parsed) return { kind: 'note', label: 'Reading…', title: 'The file is being read; it is scored once it has been read.' }
  return { kind: 'note', label: 'Scoring…', title: 'SBAM is reading the file against the goal. This takes a few seconds.' }
}
