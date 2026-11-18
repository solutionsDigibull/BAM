// Upload results (16 Oct 2026): a document type and a source lane per file, read from the file name and extension (no
// classifier exists in this build). The Confidence column no longer comes from here: it is the backend's relevance to
// the goal, read from the file's content (#802, `lib/relevance.ts`).
// ponytail: name + extension rules only; swap for the parse service's document classifier when it exists.

export type Lane = 'Documents' | 'Sheets' | 'Images' | 'Other'
export const LANES: Lane[] = ['Documents', 'Sheets', 'Images', 'Other']

const ext = (name: string) => (name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '')
const DOC = ['pdf', 'docx', 'doc', 'pptx', 'txt', 'eml', 'rtf'], SHEET = ['xlsx', 'xls', 'csv'], IMAGE = ['png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'jp2']

export function laneOf(name: string): Lane {
  const e = ext(name)
  return DOC.includes(e) ? 'Documents' : SHEET.includes(e) ? 'Sheets' : IMAGE.includes(e) ? 'Images' : 'Other'
}

/** Keyword rules, most specific first; `lanes` are the lanes where that type is expected. */
const RULES: { type: string; re: RegExp; lanes: Lane[] }[] = [
  { type: 'BOM', re: /\bbom\b|bill[\s_-]?of[\s_-]?mat/i, lanes: ['Sheets'] },
  { type: 'Quote', re: /quot|pricing|price[\s_-]?list|offer/i, lanes: ['Documents', 'Sheets'] },
  { type: 'RFQ', re: /\brfq|request[\s_-]?for|tender|package/i, lanes: ['Documents'] },
  { type: 'Drawing', re: /draw|\bdrg\b|\bdwg\b|cad|sketch|blueprint/i, lanes: ['Images', 'Documents', 'Other'] },
  { type: 'Spec', re: /spec|requirement|standard|qa[\s_-]?\d/i, lanes: ['Documents'] },
  { type: 'Email', re: /e-?mail|thread|correspondence/i, lanes: ['Documents'] },
  { type: 'Terms', re: /terms|contract|agreement|compliance/i, lanes: ['Documents'] },
]
const BY_LANE: Record<Lane, string> = { Documents: 'Document', Sheets: 'Sheet', Images: 'Drawing', Other: 'Archive' }

export interface Kind { type: string; lane: Lane }
export function detectKind(name: string): Kind {
  const lane = laneOf(name)
  const base = name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ')
  const rule = RULES.find(r => r.re.test(base))
  return { type: rule ? rule.type : BY_LANE[lane], lane }
}
