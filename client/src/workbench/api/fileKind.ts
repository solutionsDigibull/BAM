// Upload results (16 Oct 2026): a detected document type, a confidence and a source lane per file. No classifier
// service exists in this build, so this is a rule-based reading of the file name and extension: a name keyword that
// agrees with the extension scores high, a keyword alone lower. Without a keyword a document or sheet is still surely a
// document or sheet (80%); only a guess flags for review (below LOW_CONFIDENCE): an image read as a drawing, an
// unknown type (round 2, 21 Oct 2026: plain PDFs and sheets no longer flag a typical batch).
// ponytail: name + extension rules only; swap for the parse service's document classifier when it exists.

export type Lane = 'Documents' | 'Sheets' | 'Images' | 'Other'
export const LANES: Lane[] = ['Documents', 'Sheets', 'Images', 'Other']
/** Rows below this are flagged "Needs review" on Upload; tune here. */
export const LOW_CONFIDENCE = 70

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

export interface Kind { type: string; confidence: number; lane: Lane }
export function detectKind(name: string): Kind {
  const lane = laneOf(name)
  const base = name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ')
  const rule = RULES.find(r => r.re.test(base))
  if (rule) return { type: rule.type, confidence: rule.lanes.includes(lane) ? 92 : 76, lane }
  return { type: BY_LANE[lane], confidence: lane === 'Other' ? 55 : lane === 'Images' ? 64 : 80, lane }
}
