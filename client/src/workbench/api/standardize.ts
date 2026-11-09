// Standardize (15 Oct 2026, BuLLM Standardize diagram): classify each accepted file, keep the native original, say when
// a PDF analysis copy is needed, identify problems, and decide Yes (ready for Parse) / No (Local AI fix) / Doubtful
// (Manual review, then Local AI fix where required). Pure, so it is unit tested; the screen is steps/Standardize.tsx.
// Only checks this build can really run are run (file signatures read from the uploaded bytes). Header/template checks
// need the parse service and conversion and the Local AI fix need their services: none is connected, so those are
// reported as not available, never as passed.
import { ACCEPTED_TYPES } from './types'

export type Route = 'direct' | 'pdf-copy' | 'unsupported'
/** The routing reference from the diagram. Accepted types still come from ACCEPTED_TYPES, never from this list. */
export const DIRECT_REF = 'CSV / XLS / XLSX · Gerber 274-X / 274-D / X2 / X3 · PDF · ZIP'
export const PDF_FIRST_REF = 'EML · TIFF / JPEG / JP2 / PNG / BMP · STP / STEP · DWG / DXF'
const PDF_FIRST = ['.eml', '.tif', '.tiff', '.jpg', '.jpeg', '.jp2', '.png', '.bmp', '.stp', '.step', '.dwg', '.dxf']
export const extOf = (name: string) => (name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '')

export function classify(name: string): Route {
  const ext = extOf(name)
  if (!ACCEPTED_TYPES.includes(ext)) return 'unsupported'
  return PDF_FIRST.includes(ext) ? 'pdf-copy' : 'direct'
}

export const ISSUES = [
  { id: 'wrong-template', label: 'Wrong template' }, { id: 'headers-mismatch', label: 'Headers mismatch' },
  { id: 'duplicate-headers', label: 'Duplicate headers' }, { id: 'header-typo', label: 'Header typo' },
  { id: 'unnecessary-info', label: 'Unnecessary info' }, { id: 'missing-data', label: 'Missing data' },
  { id: 'wrong-extension', label: 'Wrong extension' }, { id: 'cell-borders', label: 'Cell borders' },
  { id: 'excessive-size', label: 'Excessive size' }, { id: 'corrupted', label: 'Corrupted' },
  { id: 'password', label: 'Password protected' }, { id: 'unsupported', label: 'Unsupported format' },
] as const
export type IssueId = (typeof ISSUES)[number]['id']
export const issueLabel = (id: IssueId) => ISSUES.find(i => i.id === id)!.label
/** Checks that need the parse service (table headers, templates, content): not run in this build. */
export const NEEDS_PARSER: IssueId[] = ['wrong-template', 'headers-mismatch', 'duplicate-headers', 'header-typo', 'unnecessary-info', 'missing-data', 'cell-borders']
/** A doubtful finding goes to Manual review; every other finding is a known problem for the Local AI fix. */
const DOUBTFUL: IssueId[] = ['wrong-extension']

export const PRE_PDF = ['Unsupported Model Version', 'External Reference (XREF) Missing', 'Corrupt Geometry Kernel', 'Empty Layers/Invisible Geometry', 'Missing SHX/Engineering Fonts', 'File Extension Mismatch', 'Broken Assembly Tree']
export const POST_PDF = ['Rasterization (Flattening)', 'Non-Searchable Text (Glyph Error)', 'Scale/Reference Missing', 'Overlapping Annotations', 'Multi-Sheet Confusion', 'Line Weight Saturation', 'Title Block Data Gap', 'GD&T Symbol Ambiguity', 'BOM-to-Geometry Mismatch', 'Missing Tolerance Specs', 'Note/Process Omission']

// ---- signature checks on the real bytes ---------------------------------------------------------------------------
const starts = (b: Uint8Array, sig: number[]) => sig.every((x, i) => b[i] === x)
const ascii = (b: Uint8Array) => { let s = ''; for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]!); return s }
const MAGIC: { kind: string; sig: number[] }[] = [
  { kind: 'pdf', sig: [0x25, 0x50, 0x44, 0x46, 0x2d] }, { kind: 'zip', sig: [0x50, 0x4b, 0x03, 0x04] }, { kind: 'zip', sig: [0x50, 0x4b, 0x05, 0x06] },
  { kind: 'cfb', sig: [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] }, { kind: 'png', sig: [0x89, 0x50, 0x4e, 0x47] },
  { kind: 'jpeg', sig: [0xff, 0xd8, 0xff] }, { kind: 'exe', sig: [0x4d, 0x5a] },
]
const kindOf = (b: Uint8Array) => MAGIC.find(m => starts(b, m.sig))?.kind
/** ZIP local header: general-purpose bit 0 set means the entry is encrypted. */
const zipEncrypted = (b: Uint8Array) => b.length > 7 && (b[6]! & 1) === 1

/** Problems found in a file's first and last bytes (`head`, `tail`: up to 64 KB each). */
export function sniff(name: string, size: number, head: Uint8Array, tail: Uint8Array = head): IssueId[] {
  const ext = extOf(name), kind = kindOf(head)
  if (classify(name) === 'unsupported') return ['unsupported']
  if (!size || !head.length) return ['corrupted']
  const other = (expected: string) => (kind && kind !== expected ? 'wrong-extension' : 'corrupted') as IssueId
  switch (ext) {
    case '.pdf':
      if (kind !== 'pdf') return [other('pdf')]
      return /\/Encrypt\b/.test(ascii(head) + ascii(tail)) ? ['password'] : []
    case '.docx': case '.xlsx': case '.pptx':
      if (kind === 'cfb') return ['password'] // an encrypted Office document is stored as a CFB container
      if (kind !== 'zip') return [other('zip')]
      if (zipEncrypted(head)) return ['password']
      // Every Office Open XML package lists [Content_Types].xml (first entry or the central directory at the end).
      return (ascii(head) + ascii(tail)).includes('[Content_Types].xml') ? [] : ['wrong-extension']
    case '.zip':
      if (kind !== 'zip') return [other('zip')]
      return zipEncrypted(head) ? ['password'] : []
    case '.png':
      return kind === 'png' ? [] : [other('png')]
    case '.txt':
      if (kind) return ['wrong-extension']
      return head.subarray(0, 8192).includes(0) ? ['wrong-extension'] : []
    default: return []
  }
}

// ---- decision ----------------------------------------------------------------------------------------------------
export type FileState = 'checking' | 'ready' | 'manual' | 'fix' | 'failed'
export interface Assessment { route: Route; issues: IssueId[]; state: FileState; reason: string; reviewed?: boolean }
export const STATE_TEXT: Record<FileState, string> = { checking: 'Checking', ready: 'Ready', manual: 'Manual review', fix: 'Local AI fix', failed: 'Failed' }
export const FIX_UNAVAILABLE = 'Local AI fix is not available yet. Remove the file in Upload and upload a corrected copy.'
export const CONVERT_UNAVAILABLE = 'A PDF analysis copy cannot be made for this file yet. A person reviews the original instead.'

/** Yes / No / Doubtful. `reviewed` = the FDE accepted the doubtful findings in Manual review (the original is unchanged). */
export function decide(route: Route, issues: IssueId[] | undefined, opts: { reviewed?: boolean; unreadable?: boolean } = {}): Assessment {
  if (opts.unreadable) return { route, issues: [], state: 'failed', reason: 'The file content is not available in this session. Remove it in Upload and add it again.' }
  if (!issues) return { route, issues: [], state: 'checking', reason: 'Reading the file' }
  const known = issues.filter(i => !DOUBTFUL.includes(i)), doubtful = issues.filter(i => DOUBTFUL.includes(i))
  if (known.length) return { route, issues, state: 'fix', reason: known.map(issueLabel).join(', ') }
  if (doubtful.length && !opts.reviewed) return { route, issues, state: 'manual', reason: `${doubtful.map(issueLabel).join(', ')}: needs a person to decide` }
  // PDF-copy-first files (images): no conversion service, so no analysis copy exists. Doubtful, never failed or silently
  // ready: a person reviews the original and may accept it as-is (recorded; nothing is converted or changed).
  if (route === 'pdf-copy' && !opts.reviewed) return { route, issues, state: 'manual', reason: 'Held - needs review before parse' }
  return { route, issues, state: 'ready', reviewed: !!opts.reviewed && (doubtful.length > 0 || route === 'pdf-copy'),
    reason: route === 'pdf-copy' ? 'Accepted in manual review · original used, no analysis copy' : doubtful.length ? 'Accepted in manual review · original unchanged' : 'File checks passed' }
}
