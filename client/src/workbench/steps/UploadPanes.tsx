// Ingestion > Upload (16 Oct 2026, Upload three-pane wireframes): files on the left (NavRail), the conversation and a
// drop zone in the middle (AssistantPane), results and the one action on the right (the workspace). Every part reads
// the same upload store (stored uploads + the uploader's in-flight files), so list, table, lanes and quota agree.
// All three entry points (+ Add files, the left-pane drop, the middle drop zone) go through Intake's existing pick
// handler: triage, the review dialog, then the existing resumable uploader.
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useUppyState } from '@uppy/react'
import { api } from '../api/adapter'
import { ACCEPTED_TYPES, MAX_BATCH_BYTES, type Snapshot } from '../api/types'
import { SYSTEM_FILE_LABEL, isReleased } from '../api/scanGate'
import { LANES, detectKind, type Kind } from '../api/fileKind'
import { archive, setUi, uppy, useUi } from '../lib/store'
import { plural } from '../lib/bamUi'
import { useAction } from '../lib/data'
import { Confirm, Icon, Tip } from '../components/ui'
import { fdeView } from './IngestionFlow'
import { UploadIdle } from './UploadIdle'
import { addFilesLocked, canAddFiles, resetGoal, useGoal } from '../lib/goal'
import { useReviewFiles } from './Standardize'

// ---- entry points --------------------------------------------------------------------------------------------
type Pick = (files: FileList, source: 'files' | 'folder' | 'zip' | 'drop') => void
let handler: Pick | null = null
/** Intake registers its triage + review handler while Upload is open. */
export const setPickHandler = (h: Pick | null) => { handler = h }
export const pickFiles: Pick = (files, source) => { if (files.length && !addFilesLocked()) handler?.(files, source) }
/** files = multiple selection; folder = every file inside (subfolders included; triage keeps the supported ones); zip = one archive. */
export function openPicker(mode: 'files' | 'folder' | 'zip' = 'files') {
  if (addFilesLocked()) return // locked until the goal is approved: no picker by any route
  const i = document.createElement('input')
  i.type = 'file'
  if (mode === 'folder') { i.setAttribute('webkitdirectory', ''); i.multiple = true }
  else if (mode === 'zip') i.accept = '.zip'
  else { i.multiple = true; i.accept = ACCEPTED_TYPES.join(',') }
  i.onchange = () => { if (i.files) pickFiles(i.files, mode) }
  i.click()
}
export const HELPER = `${ACCEPTED_TYPES.filter(t => t !== '.zip').map(t => t.slice(1).toUpperCase()).join(', ')} and ZIP archives`

// ---- dragging (files dragged over the window) ------------------------------------------------------------------
let depth = 0, dragging = false
const dsubs = new Set<() => void>()
const setDragging = (v: boolean) => { if (dragging !== v) { dragging = v; dsubs.forEach(f => f()) } }
const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')
if (typeof window !== 'undefined') {
  window.addEventListener('dragenter', e => { if (hasFiles(e)) { depth++; setDragging(true) } })
  window.addEventListener('dragleave', e => { if (hasFiles(e)) { depth = Math.max(0, depth - 1); if (!depth) setDragging(false) } })
  for (const t of ['drop', 'dragend'] as const) window.addEventListener(t, () => { depth = 0; setDragging(false) })
}
export const useDragging = () => useSyncExternalStore(cb => { dsubs.add(cb); return () => { dsubs.delete(cb) } }, () => dragging)
/** Drop target props (left pane, middle drop zone). */
export const dropTarget = (disabled?: boolean) => ({
  onDragOver: (e: React.DragEvent) => { if (!disabled) e.preventDefault() },
  onDrop: (e: React.DragEvent) => { e.preventDefault(); if (!disabled) pickFiles(e.dataTransfer.files, 'drop') },
})

// ---- the one upload store view -------------------------------------------------------------------------------
export interface UpRow { id: string; name: string; size: number; state: 'uploading' | 'checking' | 'ready' | 'failed'; kind: Kind; reason?: string; retry?: () => void }
/** Added files (counted everywhere) vs failed ones (listed under Needs attention, never counted). */
export const isAdded = (r: UpRow) => r.state !== 'failed'
/** No file is skipped (29 Oct 2026, failed-files wireframes v2): every refused file is a failure with a reason and a
 *  fix. System files (.DS_Store, desktop.ini) and anything filtered at selection read as an unsupported type. */
const NEUTRAL_REASON = (reason: string) => reason === SYSTEM_FILE_LABEL || /^(Unsupported|Filtered out)/.test(reason) ? 'Failed - unsupported type'
  : /duplicate/i.test(reason) ? 'Failed - duplicate'
  : /^Empty/.test(reason) ? 'Failed - empty file' : /^Over/.test(reason) ? 'Failed - too large' : `Failed - ${reason.charAt(0).toLowerCase()}${reason.slice(1)}`
// Record ids the snapshot has shown. A finished upload whose record is not in the snapshot yet is in transit (counted
// as uploaded, checking); one whose record was shown and is gone was removed (not shown again).
const seen = new Set<string>()
export function useUploadRows(snap: Snapshot): UpRow[] {
  const files = useUppyState(uppy, s => s.files)
  const refused = useUi(s => s.rejected)
  const { busy, run } = useAction()
  const stored = snap.uploads.map<UpRow>(u => {
    const state: UpRow['state'] = isReleased(u) ? 'ready' : fdeView(u).pill === 'running' ? 'checking' : 'failed'
    return { id: u.id, name: u.name, size: u.size, kind: detectKind(u.name), state,
      reason: state !== 'failed' ? undefined : u.scanError ? 'Failed - check did not finish' : u.status === 'failed' ? 'Failed - upload error' : 'Failed - cannot be used',
      retry: u.scanError && snap.steps[0]!.editable && !busy ? () => { void run('Retry check', () => api.retryScan(u.id), { step: 1 }) } : undefined }
  })
  for (const u of snap.uploads) seen.add(u.id)
  const transit = Object.values(files).flatMap<UpRow>(f => {
    const id = (f.response?.body as { id?: string } | undefined)?.id
    // The uploader only knows a placeholder id (`pending:<name>`), never the document's, so a stored record with the same
    // name and size is this file: show it once.
    const stored = snap.uploads.some(u => u.name === f.name && u.size === f.size)
    return f.progress.uploadComplete && !f.error && id && !seen.has(id) && !stored ? [{ id, name: f.name ?? 'file', size: f.size ?? 0, kind: detectKind(f.name ?? ''), state: 'checking' }] : []
  })
  const sending = Object.values(files).filter(f => !f.progress.uploadComplete && !snap.uploads.some(u => u.name === f.name && u.size === f.size))
    .map<UpRow>(f => ({ id: f.id, name: f.name ?? 'file', size: f.size ?? 0, kind: detectKind(f.name ?? ''), state: f.error ? 'failed' : 'uploading',
      reason: f.error ? 'Failed - network error' : undefined, retry: f.error ? () => { void uppy.retryUpload(f.id) } : undefined }))
  const skipped = refused.map<UpRow>((r, i) => ({ id: `refused-${i}`, name: r.name, size: 0, kind: detectKind(r.name), state: 'failed', reason: NEUTRAL_REASON(r.reason) }))
  const added = [...stored, ...transit, ...sending]
  return [...added.filter(isAdded), ...added.filter(r => !isAdded(r)), ...skipped]
}
/** The one file counter (round 2, #7): every count on Upload (chat, status line, footer, progress bar, assistant
 *  replies) derives from it, so they show the same numbers from the same rows. */
export function fileCounts(rows: UpRow[]) {
  const added = rows.filter(isAdded)
  return { totalFiles: added.length, uploadedCount: added.filter(r => r.state !== 'uploading').length }
}
/** Live upload count (#5): null when nothing is sending. */
export function uploadCount(rows: UpRow[]) {
  const c = fileCounts(rows)
  return c.uploadedCount < c.totalFiles ? { done: c.uploadedCount, total: c.totalFiles } : null
}
/** "38 of 45 uploaded" with a progress bar. */
export const UploadProgress = ({ done, total }: { done: number; total: number }) => (
  <span className="up-progress"><span>{done} of {total} uploaded</span>
    <progress max={total} value={done} aria-label={`${done} of ${total} files uploaded`} /></span>
)
/** Short reason for a failed / skipped row, for the chat summary: "Failed - unsupported type" → "unsupported type". */
const shortReason = (r: UpRow) => (r.reason ?? 'not added').replace(/^(Failed|Skipped|Filtered out) - /, '')

export type Phase = 'Empty' | 'Dragging' | 'Files added' | 'Ready'
export function phaseOf(rows: UpRow[], drag: boolean): Phase {
  if (drag) return 'Dragging'
  rows = rows.filter(isAdded)
  if (!rows.length) return 'Empty'
  return rows.some(r => r.state === 'uploading' || r.state === 'checking') ? 'Files added' : 'Ready'
}

// ---- left pane: Uploaded files + Add files ---------------------------------------------------------------------
export function UploadFilesBlock({ snap }: { snap: Snapshot }) {
  const uploadsClosed = !snap.steps[0]!.editable
  // Goal flow (29 Oct 2026, approval gate): locked until the goal draft is approved; then highlighted until files arrive.
  const open = useGoal(canAddFiles), approved = open
  const added = fileCounts(useUploadRows(snap)).totalFiles > 0
  const closed = uploadsClosed || !open
  // The file list lives only in the right pane's results table (per user, 16 Oct 2026); the left keeps the entry point.
  return (
    <section className="up-left" aria-label="Add source files" {...dropTarget(closed)}>
      <AddFilesMenu closed={closed} cue={approved && !added && !closed} hint={!uploadsClosed && !open ? 'up-add-hint' : undefined} />
      <p className="muted xs" id="up-add-hint">{uploadsClosed ? 'Uploads are closed while the SOW is approved. Reopen Scope to add documents.' : !open ? 'Send your goal first.' : HELPER}</p>
    </section>
  )
}

/** "+ Add files" opens a small menu anchored under it: Choose folder · Bulk upload · Upload zip. Arrow keys move,
 *  Enter/Space choose, Escape or a click outside closes and returns focus to the button. */
const OPTIONS = [{ mode: 'folder', icon: 'tb-folder', label: 'Choose folder' }, { mode: 'files', icon: 'tb-files', label: 'Bulk upload' }, { mode: 'zip', icon: 'tb-file-zip', label: 'Upload zip' }] as const
function AddFilesMenu({ closed, cue, hint }: { closed: boolean; cue?: boolean; hint?: string }) {
  const [open, setOpen] = useState(false)
  const btn = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null)
  const close = (focus = true) => { setOpen(false); if (focus) btn.current?.focus() }
  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    const outside = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) close(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  const onKey = (e: React.KeyboardEvent) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])
    const i = items.indexOf(document.activeElement as HTMLButtonElement)
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus() }
    else if (e.key === 'Escape') { e.preventDefault(); close() }
  }
  return (
    <div className="up-add-wrap">
      <button ref={btn} className={`btn ${cue ? 'btn-primary up-add-cue' : 'btn-outline'} up-add`} aria-haspopup="menu" aria-expanded={open} aria-controls="up-add-menu"
        aria-disabled={closed || undefined} aria-describedby={hint} disabled={closed} onClick={() => setOpen(o => !o)}><Icon name="plus" size={14} />Add files</button>
      {open && <div ref={menu} id="up-add-menu" className="up-add-menu" role="menu" aria-label="Add files" onKeyDown={onKey}>
        {OPTIONS.map(o => <button key={o.mode} role="menuitem" className="up-add-item" onClick={() => { close(); openPicker(o.mode) }}><Icon name={o.icon} size={16} />{o.label}</button>)}
      </div>}
    </div>
  )
}

// ---- middle pane: drop zone + the assistant's state messages ---------------------------------------------------
export function UploadDropZone({ snap }: { snap: Snapshot }) {
  const drag = useDragging()
  const closed = !snap.steps[0]!.editable
  return <>
    <div className={`up-drop ${drag && !closed ? 'on' : ''}`} role="button" tabIndex={closed ? -1 : 0} aria-disabled={closed || undefined}
      aria-label="Drop more files here or browse" {...dropTarget(closed)}
      onClick={() => { if (!closed) openPicker('files') }} onKeyDown={e => { if (!closed && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openPicker('files') } }}>
      <Icon name="cloud-up" size={20} />
      <strong>{drag && !closed ? 'Release to add files' : 'Drop more files here or browse'}</strong>
      <span className="muted xs">Resumable upload</span>
    </div>
    <p className="muted xs up-hint">Add files on the left or continue the conversation.</p>
  </>
}

/** The readiness summary once every added file is uploaded and checked. `key` is the upload result only
 *  (added:failed): AssistantPane posts `text` once per key (round 3, #3), never because the stage changed.
 *  `live` is not posted: the newest summary message renders it from current state (round 4), so the review count
 *  (`useReviewFiles` in Standardize.tsx) is always current and the tail shows only while Upload is the active stage. */
export function uploadSummary(all: UpRow[], review: number, uploadActive = true): { key: string; text: string; live: string } | null {
  const rows = all.filter(isAdded)
  if (!rows.length || rows.some(r => r.state === 'uploading' || r.state === 'checking')) return null
  const { totalFiles: n } = fileCounts(all)
  const failed = all.filter(r => r.state === 'failed')
  const list = (rs: UpRow[]) => rs.map(r => `${r.name} (${shortReason(r)})`).join(', ')
  const text = `${plural(n, 'file')} added (see files panel)`
    + (failed.length ? `; ${failed.length} couldn't be added: ${list(failed)}` : '') + '.'
  const live = [review ? `${plural(review, 'file')} ${review === 1 ? 'needs' : 'need'} review before parse.` : '',
    uploadActive ? 'Continue when you are ready. Have more source files? Drop them here.' : ''].filter(Boolean).join(' ')
  return { key: `${n}:${failed.length}`, text, live }
}

/** Live lines under the thread: the empty prompt and the in-flight counter. The finished summary is a thread message. */
export function UploadMessages({ snap }: { snap: Snapshot }) {
  const all = useUploadRows(snap)
  const rows = all.filter(isAdded)
  const drag = useDragging()
  const objective = useUi(s => (s.fdeGoals ?? '').trim())
  const phase = phaseOf(all, drag)
  const { totalFiles: n, uploadedCount } = fileCounts(all), s = n === 1 ? '' : 's'
  const msgs: string[] = []
  if (!n) msgs.push(phase === 'Dragging' ? 'The first files go on the left. You can add more files here at any time.'
    : 'Add your first files with + Add files on the left. What would you like this RFQ pack to help you do?')
  else if (rows.some(r => r.state === 'uploading' || r.state === 'checking'))
    msgs.push(`${uploadedCount < n ? `${uploadedCount} of ${n} uploaded` : `${n} file${s} uploaded; checking them`}.${objective ? '' : ' Tell me what you want to prepare from this pack.'}`)
  return <>{msgs.map(m => <div key={m} className="as-msg as-bot" role="status">{m}</div>)}</>
}

// ---- right pane: instructions, results, lanes, quota ---------------------------------------------------------
/** A failed row's inline reason and its fix (29 Oct 2026, failed-file wireframes): Replace + Discard when the file
 *  itself is the problem, Retry + Discard when the upload or its check was cut short. */
function fixOf(r: UpRow): { title: string; line: string; retry: boolean } {
  const why = r.reason ?? ''
  if (/unsupported/i.test(why)) return { title: 'Unsupported type.', line: 'Replace with a supported file format.', retry: false }
  if (/empty/i.test(why)) return { title: 'Empty file.', line: 'The file has no content to parse.', retry: false }
  if (/duplicate/i.test(why)) return { title: 'Duplicate.', line: 'A file with the same name and size is already in the list.', retry: false }
  if (/too large|over \d/i.test(why)) return { title: 'Too large.', line: 'Replace it with a smaller file or split it.', retry: false }
  if (/network|upload error|upload failed/i.test(why)) return { title: 'Upload interrupted.', line: 'The connection dropped before the file finished.', retry: !!r.retry }
  if (/did not finish/i.test(why)) return { title: 'Check did not finish.', line: 'Run the file check again.', retry: !!r.retry }
  return { title: 'Cannot be used.', line: 'Replace it with a readable copy of the file.', retry: !!r.retry }
}
/** Removes a row: stored files through the backend (same gate as Reset), in-flight ones from the uploader, refused ones from the list. */
async function discard(snap: Snapshot, row: UpRow) {
  archive(row.name, row.reason || 'Removed from the upload list')
  if (snap.uploads.some(u => u.id === row.id)) await api.removeUpload(row.id)
  else if (uppy.getFile(row.id)) uppy.removeFile(row.id)
  else if (row.id.startsWith('refused-')) { const i = Number(row.id.slice(8)); setUi(s => ({ rejected: s.rejected.filter((_, j) => j !== i) })) }
}
/** "24 files · 4 need attention" beside the pane title, counted from the same rows as the list. */
export function UploadCount({ snap }: { snap: Snapshot }) {
  const all = useUploadRows(snap)
  const n = all.filter(isAdded).length, attn = all.filter(r => r.state === 'failed').length
  if (!all.length) return null
  return <span className="up-count">{plural(n, 'file')}{attn > 0 && <> · <span className="up-attn-n">{attn} need{attn === 1 ? 's' : ''} attention</span></>}</span>
}
const GENERIC = new Set(['Document', 'Sheet', 'Drawing', 'Archive'])

export function UploadResults({ snap }: { snap: Snapshot }) {
  const all = useUploadRows(snap)
  const rows = all.filter(isAdded), failed = all.filter(r => r.state === 'failed')
  const review = useReviewFiles(snap).ids
  const archived = useUi(s => s.archived)
  const { busy: acting, run } = useAction()
  const closed = !snap.steps[0]!.editable
  const busy = rows.some(r => r.state === 'uploading' || r.state === 'checking')
  const progress = uploadCount(all)
  const bytes = rows.reduce((t, r) => t + r.size, 0)
  const pct = Math.min(100, Math.round(bytes / MAX_BATCH_BYTES * 100))
  const pctText = bytes && pct < 1 ? '<1%' : `${pct}%`
  const [menu, setMenu] = useState<{ row: UpRow; x: number; y: number } | null>(null)
  const rowMenu = (r: UpRow) => ({ tabIndex: 0, className: menu?.row.id === r.id ? 'up-sel' : undefined,
    onContextMenu: (e: React.MouseEvent) => { e.preventDefault(); setMenu({ row: r, x: e.clientX, y: e.clientY }) },
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => { if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault(); const b = e.currentTarget.getBoundingClientRect(); setMenu({ row: r, x: b.left + b.width / 3, y: b.bottom }) } } })
  // Zero files: the skeleton state (returns whenever the pane is back to zero). The first file crossfades it (~300ms).
  const [leaving, setLeaving] = useState(false)
  const had = useRef(all.length > 0)
  useEffect(() => { const has = all.length > 0; if (has && !had.current) setLeaving(true); had.current = has }, [all.length])
  useEffect(() => { if (!leaving) return; const t = setTimeout(() => setLeaving(false), 300); return () => clearTimeout(t) }, [leaving])
  if (!all.length) return (
    <main className="up-right" aria-labelledby="center-title">
      <h2 id="center-title" className="sr-only" tabIndex={-1}>Uploaded files</h2>
      <UploadIdle />
    </main>
  )
  return (
    <main className="up-right" aria-labelledby="center-title">
      <h2 id="center-title" className="sr-only" tabIndex={-1}>Uploaded files</h2>
      {leaving && <UploadIdle leaving />}
      {/* Failures first, each with its reason always visible and its fix (29 Oct 2026). */}
      {failed.length > 0 && <section className="up-attn" aria-labelledby="up-attn-h">
        <h3 id="up-attn-h" className="up-sec">Needs attention</h3>
        {failed.map(r => { const f = fixOf(r), m = rowMenu(r)
          return <div key={r.id} {...m} className={`up-attn-card${m.className ? ` ${m.className}` : ''}`}>
            <Icon name="tb-alert-triangle" size={16} />
            <div className="up-attn-body"><strong title={r.name}>{r.name}</strong><p><b>{f.title}</b> {f.line}</p></div>
            <div className="up-attn-act">
              {f.retry ? <button className="btn btn-outline sm" disabled={!!acting} onClick={r.retry}><Icon name="tb-refresh" size={14} />Retry</button>
                : <button className="btn btn-primary sm" disabled={!!acting || closed} onClick={() => void run('Replace', async () => { await discard(snap, r); openPicker('files') }, { step: 1 })}>Replace</button>}
              <button className="btn btn-ghost sm" disabled={!!acting} onClick={() => void run('Discard', () => discard(snap, r), { step: 1 })}>Discard</button>
            </div>
          </div> })}
      </section>}
      {rows.length > 0 && <div className="up-table-wrap">
        {failed.length > 0 && <h3 className="up-sec up-sec-in">Uploaded</h3>}
        <table className="table up-table">
          <colgroup><col style={{ width: '2.5em' }} /><col /><col className="up-col-type" /><col className="up-col-conf" /></colgroup>
          <thead><tr><th scope="col" className="up-num">#</th><th scope="col">File name</th><th scope="col">Type</th>
            <th scope="col" className="up-conf"><Tip text="Extraction confidence"><span className="up-th-tip" tabIndex={0}>Confidence</span></Tip></th></tr></thead>
          <tbody>{rows.map((r, i) => {
            const done = r.state === 'ready', held = review.has(r.id), c = r.kind.confidence
            const m = rowMenu(r)
            return <tr key={r.id} {...m} className={[held ? 'up-low' : '', m.className].filter(Boolean).join(' ') || undefined}>
              <td className="up-num">{i + 1}</td>
              <th scope="row" title={r.name}><span className="up-fname"><Icon name="tb-file" size={14} /><span>{r.name}</span></span>
                {held && <span className="up-review" role="note" aria-label="Needs review before parse" title="Held for review before parse"><Icon name="alert" size={12} /><span aria-hidden>Needs review</span></span>}</th>
              <td><span className={`up-type${GENERIC.has(r.kind.type) ? ' plain' : ''}`}>{r.kind.type}</span></td>
              <td className="up-conf" title={done ? 'Rule-based reading of the file name and type' : undefined}>
                {done ? <span className={`up-cbar${c >= 90 ? ' hi' : ''}`}><i aria-hidden><i style={{ width: `${c}%` }} /></i><span>{c}%</span></span> : '-'}</td>
            </tr>
          })}
          </tbody>
        </table>
      </div>}
      {all.length > 0 && <p className="up-status" role="status">{progress ? <UploadProgress {...progress} /> : busy ? 'Checking the uploaded files...' : `${rows.length} file${rows.length === 1 ? '' : 's'} uploaded${failed.length ? ',' : ''}`}
        {failed.length > 0 && <span className="tone-err"> {failed.length} failed</span>}</p>}
      <section aria-labelledby="up-lanes"><h3 id="up-lanes" className="up-label">Source lanes</h3>
        <ul className="up-lanes">{LANES.map(l => <li key={l}><strong>{rows.filter(r => r.kind.lane === l).length}</strong><span>{l}</span></li>)}</ul></section>
      <section className="up-quota"><span>Space used <strong>{pctText}</strong> of engagement quota</span>
        <span className="up-bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Space used"><span style={{ width: `${pct}%` }} /></span></section>
      {archived.length > 0 && <details className="up-archive">
        <summary><strong>Archive</strong><span className="dc-count">{archived.length}</span><Icon name="down" size={13} /></summary>
        <p className="muted xs">Discarded files stay listed here. Removing a file from the list never deletes its data.</p>
        <ul className="dc-files">{archived.map((a, i) => <li key={i}><Icon name="tb-file" size={15} /><span className="dc-name">{a.name}</span><span className="muted xs">{a.reason}</span></li>)}</ul>
      </details>}
      {menu && <RowMenu snap={snap} {...menu} onClose={() => setMenu(null)} />}
    </main>
  )
}

/** Right-click (or the Menu key / Shift+F10) on a results row: one destructive action, Delete file (23 Oct 2026,
 *  row context-menu wireframes). The row stays highlighted (`.up-sel`) while the menu is open. Stored files go through
 *  the backend's removeUpload (same gate as Reset); in-flight and refused rows are dropped from the uploader / list. */
function RowMenu({ snap, row, x, y, onClose }: { snap: Snapshot; row: UpRow; x: number; y: number; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const { busy, run } = useAction()
  const stored = snap.uploads.find(u => u.id === row.id)
  const why = !snap.steps[0]!.editable ? 'Uploads are closed.' : snap.ingestion && stored ? 'This file is already handed on.'
    : row.state === 'checking' && !stored?.scanError ? 'Wait for the file check to finish.' : null
  const trigger = useRef(document.activeElement as HTMLElement | null)
  const close = () => { onClose(); trigger.current?.focus?.() }
  useEffect(() => {
    ref.current?.querySelector('button')?.focus()
    const out = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) onClose() }
    document.addEventListener('pointerdown', out); window.addEventListener('scroll', onClose, true); window.addEventListener('resize', onClose)
    return () => { document.removeEventListener('pointerdown', out); window.removeEventListener('scroll', onClose, true); window.removeEventListener('resize', onClose) }
  }, [])
  const del = () => run('Delete file', async () => { await discard(snap, row); close() }, { step: 1 })
  // Kept inside the viewport (menu is ~210×60 incl. a disabled reason).
  const left = Math.min(x, window.innerWidth - 220), top = Math.min(y + 4, window.innerHeight - 90)
  return (
    <div ref={ref} className="up-ctx" role="menu" aria-label={`Actions for ${row.name}`} style={{ left, top }} onKeyDown={e => { if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); close() } }}>
      <button role="menuitem" className="up-ctx-item" disabled={!!why || !!busy} aria-describedby={why ? 'up-ctx-why' : undefined} onClick={del}>
        <Icon name="tb-trash" size={18} />{busy ? 'Deleting...' : 'Delete file'}</button>
      {why && <p id="up-ctx-why" className="muted xs">{why}</p>}
    </div>
  )
}

/** Overflow "..." in the Uploaded files header: Reset removes every file of this intake and clears the objective. */
export function UploadMenu({ snap }: { snap: Snapshot }) {
  const [open, setOpen] = useState(false), [ask, setAsk] = useState(false)
  const { busy, run } = useAction()
  const locked = !snap.steps[0]!.editable || !!snap.ingestion
  useEffect(() => { if (!open) return; const close = () => setOpen(false); document.addEventListener('click', close); return () => document.removeEventListener('click', close) }, [open])
  const reset = () => run('Reset', async () => {
    uppy.cancelAll()
    for (const u of snap.uploads) await api.removeUpload(u.id)
    setUi({ fdeGoals: '', rejected: [] })
    resetGoal()
  }, { step: 1 })
  return (
    <span className="up-menu">
      <button className="icon-btn sm" aria-label="More actions" aria-haspopup="menu" aria-expanded={open} onClick={e => { e.stopPropagation(); setOpen(o => !o) }}><Icon name="tb-dots" size={16} /></button>
      {open && <div className="profile-menu up-menu-list" role="menu">
        <button className="profile-item" role="menuitem" disabled={locked || !!busy} onClick={() => setAsk(true)}><Icon name="refresh" size={14} />Reset</button>
        {locked && <p className="muted xs">{snap.ingestion ? 'These files are already handed on.' : 'Uploads are closed.'}</p>}
      </div>}
      <Confirm open={ask} title="Reset this upload?" confirmLabel="Reset" onClose={() => setAsk(false)} onConfirm={() => { setAsk(false); void reset() }}
        consequences={[`Removes ${snap.uploads.length} uploaded file${snap.uploads.length === 1 ? '' : 's'} and cancels any upload in progress.`, 'Clears the goal, its draft and the instructions captured from the conversation.', 'The conversation itself is kept.']} />
    </span>
  )
}
