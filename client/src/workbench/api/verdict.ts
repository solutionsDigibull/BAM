// What the backend found out about a file, in words for the FDE: the scan and type check have passed (the file is
// accepted), then the loader read it. Pure: it only rephrases the verdict the backend returned (UploadRecord.parse).
import type { UploadRecord } from './types'

export type VerdictState = 'checking' | 'ready' | 'attention' | 'failed'
export interface Verdict { state: VerdictState; label: string; reason: string; facts: string[] }
export const VERDICT_TEXT: Record<VerdictState, string> = { checking: 'Checking', ready: 'Ready', attention: 'Needs attention', failed: 'Cannot be read' }

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** The loader's findings as short facts: pages, sheets, tables and how many pages needed text recognition. */
function factsOf(p: NonNullable<UploadRecord['parse']>): string[] {
  return [p.pages ? count(p.pages, 'page') : '', p.sheets ? count(p.sheets, 'sheet') : '', p.tables ? count(p.tables, 'table') : '',
    p.ocrPages ? `${count(p.ocrPages, 'page')} read by text recognition` : ''].filter(Boolean)
}

export function fileVerdict(u: UploadRecord): Verdict {
  const v = (state: VerdictState, reason: string, facts: string[] = []): Verdict => ({ state, label: VERDICT_TEXT[state], reason, facts })
  const p = u.parse
  if (!p) return v('checking', 'Waiting for the file to be checked')
  const facts = factsOf(p)
  if (p.state === 'PARSE_FAILED') return v('failed', p.reason ?? 'The file could not be read', facts)
  if (p.state === 'PARSING') return v('checking', 'Reading the file', facts)
  if (p.unreadablePages) return v('attention', `${count(p.unreadablePages, 'page')} could not be read`, facts)
  if (p.warnings.length) return v('attention', p.warnings.join('; '), facts)
  return v('ready', 'Read successfully', facts)
}
