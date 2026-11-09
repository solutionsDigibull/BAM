// 1.1 virus-scan gate. Pure, shared by the mock backend (the server-side guard) and the UI (the
// disabled-button reason), so both always agree. A real backend must enforce the same rules itself.
import { ACCEPTED_TYPES, chunkCount, MAX_BATCH_BYTES, MAX_BATCH_FILES, MAX_UPLOAD_BYTES, type IngestionRun, type UploadRecord } from './types'

export const acceptedType = (name: string) => ACCEPTED_TYPES.includes(name.slice(name.lastIndexOf('.')).toLowerCase())

/** Clean, released and stored: the only files that may reach RAG or be opened. */
export const isReleased = (u: UploadRecord) =>
  u.status === 'stored' &&
  u.scan === 'verified' &&
  u.released &&
  !u.scanError &&
  u.clamavVerdict !== 'threat'

export interface GateContext {
  /** Files still uploading in the browser (not yet staged). */
  uploading?: number
  /** A start request is in flight. */
  starting?: boolean
  /** The RAG pipeline is running. */
  running?: boolean
  ingestion?: IngestionRun
}

/** Why "Start ingestion" is unavailable for the FULL batch (never the filtered rows), or undefined when it may start. */
export function ingestionBlock(uploads: UploadRecord[], ctx: GateContext = {}): string | undefined {
  if (ctx.starting) return 'Ingestion run is starting'
  if (ctx.running) return 'An ingestion run is already running'
  if (!uploads.length && !ctx.uploading) return 'Upload documents first'
  if (ctx.uploading) return 'Wait for uploads to finish'
  if (uploads.some(u => u.status === 'failed')) return 'Remove failed uploads to continue'
  if (uploads.some(u => u.scanError)) return 'Retry or remove files whose scan failed'
  if (uploads.some(u => u.scan === 'scanning')) return 'Wait for virus scans to finish'
  if (uploads.some(u => u.scan === 'blocked' || u.clamavVerdict === 'threat')) return 'Remove blocked files to continue'
  const clean = uploads.filter(isReleased).map(u => u.id)
  if (!clean.length) return 'Upload documents first'
  if (ctx.ingestion && clean.length === ctx.ingestion.fileIds.length && clean.every(id => ctx.ingestion!.fileIds.includes(id)))
    return 'These verified files are already ingested'
  return undefined
}

export type ScanAction = 'view' | 'remove' | 'retry'
export interface ScanView { pill: 'pass' | 'running' | 'fail' | 'needs_review'; label: string; text: string; actions: ScanAction[] }

/** What a row shows and allows. Scanning has no actions; Blocked only Remove; Verified opens only once released. */
export function scanView(u: UploadRecord): ScanView {
  if (u.scanError) return { pill: 'needs_review', label: 'Scan error', text: `${u.scanError} Not eligible until rescanned.`, actions: ['retry', 'remove'] }
  if (u.scan === 'blocked' || u.clamavVerdict === 'threat') {
    const detail = u.threat || 'Threat detected: quarantined'
    return { pill: 'fail', label: 'Blocked', text: detail, actions: ['remove'] }
  }
  if (u.scan === 'scanning') {
    const text = 'Checking the file for threats...'
    return { pill: 'running', label: 'Scanning', text, actions: [] }
  }
  const subtext = 'Clean - Ready for ingestion'
  return { pill: 'pass', label: 'Verified', text: subtext, actions: isReleased(u) ? ['view', 'remove'] : ['remove'] }
}

// ---- intake selection: staged triage ---------------------------------------------------------------
// 1. Compatibility filter: system, unsupported, empty and over-size files are excluded (never fatal).
// 2. The usable files are what remains; files over CHUNK_BYTES are sent as named 10 MB chunks.
// 3. The chosen files must fit the batch (25 files, 250 MB with what is already in it) before upload is allowed.
const SYSTEM = /^(\..*|~\$.*|thumbs\.db|desktop\.ini|icon\r)$/i
export const isSystemFile = (name: string) => SYSTEM.test(name.split('/').pop() ?? name)
/** The one wording for a system file (.DS_Store, Thumbs.db…) in the review dialog and Upload's table: never uploaded, so never "failed". */
export const SYSTEM_FILE_LABEL = 'Filtered out - system file'
export const extOf = (name: string) => (name.includes('.') ? name.slice(name.lastIndexOf('.')).toLowerCase() : '(no extension)')
const mb = (n: number) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`

export interface Triage<T> {
  total: number
  usable: T[]
  system: T[]
  unsupported: T[]
  empty: T[]
  /** Larger than a whole batch: can never be uploaded. */
  tooLarge: T[]
  /** Unsupported files grouped by extension, most common first. */
  byExt: [string, number][]
}

export function triage<T extends { name: string; size: number }>(files: ArrayLike<T>): Triage<T> {
  const r: Triage<T> = { total: files.length, usable: [], system: [], unsupported: [], empty: [], tooLarge: [], byExt: [] }
  const ext = new Map<string, number>()
  for (const f of Array.from(files)) {
    if (isSystemFile(f.name)) r.system.push(f)
    else if (!acceptedType(f.name)) { r.unsupported.push(f); ext.set(extOf(f.name), (ext.get(extOf(f.name)) ?? 0) + 1) }
    else if (f.size === 0) r.empty.push(f)
    else if (f.size > MAX_UPLOAD_BYTES) r.tooLarge.push(f)
    else r.usable.push(f)
  }
  r.byExt = [...ext].sort((a, b) => b[1] - a[1])
  return r
}

export const MAX_MULTI_BATCH_WAVES = 9
export const MAX_MULTI_BATCH_FILES = MAX_BATCH_FILES * MAX_MULTI_BATCH_WAVES // 225 files
export const MAX_MULTI_BATCH_BYTES = MAX_BATCH_BYTES * MAX_MULTI_BATCH_WAVES // 2.25 GB

export interface MultiBatchFit {
  ok: boolean
  count: number
  bytes: number
  batchCount: number
  waves: { wave: number; files: { size: number; name?: string }[]; bytes: number }[]
  reason?: string
}

export function fitMultiBatch<T extends { size: number; name?: string }>(chosen: T[]): MultiBatchFit {
  const bytes = chosen.reduce((n, f) => n + f.size, 0)
  if (!chosen.length) return { ok: false, count: 0, bytes: 0, batchCount: 0, waves: [], reason: 'Select at least one file' }
  if (chosen.length > MAX_MULTI_BATCH_FILES) {
    return { ok: false, count: chosen.length, bytes, batchCount: 0, waves: [], reason: `${chosen.length} chosen; exceeds maximum limit of ${MAX_MULTI_BATCH_FILES} files (up to 9 batches of 25). Deselect ${chosen.length - MAX_MULTI_BATCH_FILES}.` }
  }
  if (bytes > MAX_MULTI_BATCH_BYTES) {
    return { ok: false, count: chosen.length, bytes, batchCount: 0, waves: [], reason: `${mb(bytes)} chosen; exceeds maximum budget across 9 batches (${mb(MAX_MULTI_BATCH_BYTES)}). Deselect ${mb(bytes - MAX_MULTI_BATCH_BYTES)}.` }
  }
  const waves: MultiBatchFit['waves'] = []
  const batchCount = Math.max(1, Math.ceil(chosen.length / MAX_BATCH_FILES))
  for (let i = 0; i < batchCount; i++) {
    const chunk = chosen.slice(i * MAX_BATCH_FILES, (i + 1) * MAX_BATCH_FILES)
    waves.push({
      wave: i + 1,
      files: chunk,
      bytes: chunk.reduce((sum, f) => sum + f.size, 0),
    })
  }
  return { ok: true, count: chosen.length, bytes, batchCount, waves }
}

export interface Fit {
  ok: boolean
  count: number
  bytes: number
  roomFiles: number
  roomBytes: number
  reason?: string
  multiBatch?: boolean
  batchCount?: number
}
/** Whether `chosen` fits the batch next to what it already holds. */
export function fit(chosen: { size: number }[], existing = { count: 0, bytes: 0 }, opts: { allowMultiBatch?: boolean } = {}): Fit {
  const bytes = chosen.reduce((n, f) => n + f.size, 0)
  const roomFiles = MAX_BATCH_FILES - existing.count, roomBytes = MAX_BATCH_BYTES - existing.bytes
  const fitsSingle = chosen.length <= roomFiles && bytes <= roomBytes
  const batchCount = Math.max(1, Math.ceil(chosen.length / MAX_BATCH_FILES))

  if (opts.allowMultiBatch && !fitsSingle && chosen.length <= MAX_MULTI_BATCH_FILES && bytes <= MAX_MULTI_BATCH_BYTES && chosen.length > 0) {
    return { ok: true, count: chosen.length, bytes, roomFiles, roomBytes, multiBatch: true, batchCount }
  }

  const reason = !chosen.length ? 'Select at least one file'
    : chosen.length > roomFiles ? `${chosen.length} chosen; ${Math.max(roomFiles, 0)} file${roomFiles === 1 ? '' : 's'} fit${existing.count ? ` (${existing.count} already in the batch)` : ''}. Deselect ${chosen.length - Math.max(roomFiles, 0)}.`
    : bytes > roomBytes ? `${mb(bytes)} chosen; ${mb(Math.max(roomBytes, 0))} fits${existing.bytes ? ' next to the current batch' : ''}. Deselect ${mb(bytes - roomBytes)}.`
    : undefined
  return { ok: !reason, count: chosen.length, bytes, roomFiles, roomBytes, reason, multiBatch: false, batchCount: 1 }
}

/** Fill the batch in order with whatever still fits (the "auto-select" shortcut). */
export function autoPick<T extends { size: number }>(files: T[], existing = { count: 0, bytes: 0 }): T[] {
  const out: T[] = []
  let bytes = existing.bytes
  for (const f of files) {
    if (existing.count + out.length >= MAX_BATCH_FILES) break
    if (bytes + f.size <= MAX_BATCH_BYTES) { out.push(f); bytes += f.size }
  }
  return out
}

/** Display names of the 10 MB parts a file is sent in: "Doc.pdf" -> ["Doc-chunk1.pdf", ...]; [] when it goes in one piece. */
export function chunkNames(name: string, size: number): string[] {
  const n = chunkCount(size)
  if (n < 2) return []
  const dot = name.lastIndexOf('.')
  const base = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : ''
  return Array.from({ length: n }, (_, i) => `${base}-chunk${i + 1}${ext}`)
}
