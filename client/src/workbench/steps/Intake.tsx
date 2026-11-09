// Ingestion Upload and Scan (one intake component, so both share the same Uppy instance, files, batch and run:
// moving between them never uploads twice or restarts a scan). Each file is scanned automatically after upload. Only
// released, Verified files can enter RAG, and only through "Start ingestion" (the pinned footer, IngestionFlow.tsx). Every scan state comes from the backend snapshot;
// the eligibility rule is the same pure check the backend enforces (api/scanGate.ts).
// Every selection (browse, drop, many files, folder, ZIP) is triaged first: incompatible files are filtered out,
// then the user chooses what fits the batch (25 files, 250 MB) before Upload turns on.
import { useEffect, useRef, useState } from 'react'
import Uppy from '@uppy/core'
import { useUppyState } from '@uppy/react'
import Dashboard from '@uppy/react/dashboard'
import '@uppy/core/css/style.min.css'
import '@uppy/dashboard/css/style.min.css'
import { api } from '../api/adapter'
import type { ScanOutcome } from '../api/adapter'
import { ACCEPTED_TYPES, AV_NAME, REPEL_NAME, CHUNK_BYTES, chunkCount, MAX_BATCH_BYTES, MAX_BATCH_FILES, MAX_UPLOAD_BYTES, type ScanEvent, type Snapshot, type UploadRecord } from '../api/types'
import { autoPick, chunkNames, extOf, fit, triage, MAX_MULTI_BATCH_FILES, MAX_MULTI_BATCH_BYTES, SYSTEM_FILE_LABEL, type Triage } from '../api/scanGate.ts'
import type { StepProps } from '../App'
import { useAction } from '../lib/data'
import { setUi, uploadFiles, uppy, useUi } from '../lib/store'
import { demoControls } from '../lib/demo'
import { PipelineCard } from '../rag/RagScreens'
import { Dialog, Icon, Time, fmtBytes } from '../components/ui'
import { plural } from '../lib/bamUi.ts'
import { ScanTable, ScreenTitle, SecurityDetails } from './IngestionFlow'
import { UploadResults, openPicker, setPickHandler } from './UploadPanes'
import { addFilesLocked } from '../lib/goal.ts'

const TYPES_LABEL = ACCEPTED_TYPES.map(t => t.slice(1).toUpperCase()).join(', ')
const MB = (n: number) => `${Math.round(n / 1048576)} MB`
type Source = 'files' | 'folder' | 'zip' | 'drop'

/** `dupes`: usable files already in this intake (same name and size), kept so the review accounts for every file picked. */
export interface Review { source: Source; t: Triage<File>; dupes: File[] }
type Existing = { count: number; bytes: number }
// ponytail: renders the first 400 usable files; a bigger folder should be narrowed before picking.
const LIST_CAP = 400

// The review step uses Uppy's own Dashboard on a separate, upload-less Uppy instance that holds exactly the CHOSEN
// files: removing a file in the Dashboard deselects it, adding one there passes the same type/size rules. Upload
// hands the chosen File objects to the one shared uploader (lib/store.ts), so nothing else changes.
const reviewUppy = new Uppy({ id: 'bam-review', autoProceed: false, restrictions: { allowedFileTypes: ACCEPTED_TYPES, maxFileSize: MAX_UPLOAD_BYTES } })
const GRID_H = 420, GAP = 10, PAD = 12, LABEL = 32, MIN_TILE = 64, MAX_TILE = 132
/** Largest tile width (px) at which n tiles fit a box `w` wide and GRID_H tall; MIN_TILE (then scroll) past that. */
export function fitTile(n: number, w: number) {
  for (let t = MAX_TILE; t > MIN_TILE; t -= 2) {
    const cols = Math.max(1, Math.floor((w - 2 * PAD + GAP) / (t + GAP))), rows = Math.ceil(n / cols)
    if (rows * (t * 0.78 + LABEL) + (rows - 1) * GAP + 2 * PAD <= GRID_H) return t
  }
  return MIN_TILE
}
const EXT_TONE: Record<string, string> = { pdf: 'var(--err)', zip: 'var(--ok)', xlsx: 'var(--ok)', docx: 'var(--accent)', pptx: 'var(--warn)' }

/** Review tiles (replace Uppy's file grid, which is fixed at 5 per row and 200px rows): every tile shrinks as the
 *  selection grows so the whole selection fits one box. reviewUppy stays the selection; × removes from it. */
function FitGrid({ files }: { files: Record<string, { id: string; name?: string; size: number | null; data: unknown }> }) {
  const ref = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null)
  const [w, setW] = useState(900)
  useEffect(() => {
    const el = ref.current; if (!el) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width)); ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // Filename filter (display only: the selection itself never changes).
  const [q, setQ] = useState('')
  const all = Object.values(files)
  const list = q.trim() ? all.filter(f => (f.name ?? '').toLowerCase().includes(q.trim().toLowerCase())) : all
  const tile = fitTile(list.length, w)
  const add = (fl: FileList | null) => {
    if (addFilesLocked()) return
    for (const f of Array.from(fl ?? [])) try { reviewUppy.addFile({ name: f.name, type: f.type, data: f, source: 'review' }) } catch { /* dupes / restrictions: skipped */ }
    if (input.current) input.current.value = ''
  }
  return (
    <div ref={ref} className="fit-grid-wrap" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); add(e.dataTransfer.files) }}>
      <div className="fit-grid-bar">
        <input type="search" className="fit-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by file name" aria-label="Filter files by name" />
        <span className="muted xs">{TYPES_LABEL} · remove a file to leave it out of this batch</span>
        <button className="link sm" disabled={addFilesLocked()} onClick={() => { if (!addFilesLocked()) input.current?.click() }}><Icon name="plus" size={12} />Add more</button>
        <input ref={input} type="file" multiple hidden accept={ACCEPTED_TYPES.join(',')} onChange={e => add(e.target.files)} />
      </div>
      {!list.length && <p className="muted sm fit-empty" role="status">No files match</p>}
      <ul className="tile-grid fit" hidden={!list.length} style={{ '--tile': `${tile}px`, maxHeight: GRID_H } as React.CSSProperties} aria-label={`${list.length} chosen files`}>
        {list.map(f => {
          const ext = extOf(f.name ?? '').replace('.', '')
          return <li key={f.id} className="tile on" style={{ '--ft': EXT_TONE[ext] ?? 'var(--muted)' } as React.CSSProperties} title={f.name}>
            <div className="tile-preview"><span className="tile-doc"><span className="tile-ext">{ext.toUpperCase()}</span></span>
              <button className="tile-x" aria-label={`Remove ${f.name}`} onClick={() => reviewUppy.removeFile(f.id)}><Icon name="x" size={10} /></button>
            </div>
            <span className="tile-name">{f.name}</span>
            <span className="tile-size">{fmtBytes(f.size ?? 0)}</span>
          </li>
        })}
      </ul>
    </div>
  )
}
function setReviewFiles(files: File[]) {
  reviewUppy.clear()
  if (!files.length) return
  try {
    reviewUppy.addFiles(files.slice(0, LIST_CAP).map(f => ({ name: f.name, type: f.type, data: f, source: 'review', meta: { relativePath: f.webkitRelativePath || null } })))
  } catch { /* restriction failures are shown by the Dashboard */ }
}

type RowStatus = 'usable' | 'failed' | 'filtered'
interface SelRow { file: File; status: RowStatus; reason?: string; label?: string }
/** One list for every file in the selection; the rows, the summary and the status filter all read it, so they add up. */
function selectionRows({ t, dupes }: Review): SelRow[] {
  return [
    ...t.usable.map(file => ({ file, status: 'usable' as const })),
    ...t.unsupported.map(file => ({ file, status: 'failed' as const, reason: `Unsupported type (${extOf(file.name)})` })),
    ...t.empty.map(file => ({ file, status: 'failed' as const, reason: 'Empty file' })),
    ...t.tooLarge.map(file => ({ file, status: 'failed' as const, reason: `Too large (over ${MB(MAX_UPLOAD_BYTES)})` })),
    ...dupes.map(file => ({ file, status: 'filtered' as const, reason: 'Duplicate' })),
    ...t.system.map(file => ({ file, status: 'filtered' as const, label: SYSTEM_FILE_LABEL })),
  ]
}
const STATUS_LABEL: Record<RowStatus, string> = { usable: 'Usable', failed: 'Failed', filtered: 'Filtered out' }
const STATUS_ICON: Record<RowStatus, string> = { usable: 'check', failed: 'alert', filtered: 'minus' }
const countBy = (rows: SelRow[]) => ({ usable: rows.filter(r => r.status === 'usable').length, failed: rows.filter(r => r.status === 'failed').length, filtered: rows.filter(r => r.status === 'filtered').length })
export const selectionSummary = (rows: SelRow[]) => { const n = countBy(rows); return `${plural(rows.length, 'file')}: ${n.usable} usable, ${n.failed} failed, ${n.filtered} filtered out` }

/** Every picked file with its status and reason, filterable by status and name. Only usable files can be chosen. */
function SelectionList({ rows }: { rows: SelRow[] }) {
  const [status, setStatus] = useState<RowStatus | 'all'>('all')
  const [q, setQ] = useState('')
  const n = countBy(rows)
  const query = q.trim().toLowerCase()
  const shown = rows.filter(r => (status === 'all' || r.status === status) && (!query || r.file.name.toLowerCase().includes(query)))
  return (
    <section className="sel-list-wrap" aria-label="All selected files">
      <div className="fit-grid-bar">
        <strong className="sel-summary" role="status">{selectionSummary(rows)}</strong>
        <span className="sel-controls">
          <select className="fit-search sel-status" value={status} onChange={e => setStatus(e.target.value as RowStatus | 'all')} aria-label="Filter files by status">
            <option value="all">All ({rows.length})</option>
            <option value="usable">Usable ({n.usable})</option>
            <option value="failed">Failed ({n.failed})</option>
            <option value="filtered">Filtered out ({n.filtered})</option>
          </select>
          <input type="search" className="fit-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Filter by file name" aria-label="Filter all selected files by name" />
        </span>
      </div>
      {shown.length
        ? <ul className="sel-list">
          {shown.map((r, i) => <li key={`${r.file.name}|${r.file.size}|${i}`} className={`sel-row is-${r.status}`}>
            <span className="sel-name" title={r.file.webkitRelativePath || r.file.name}>{r.file.name}</span>
            <span className="muted xs">{fmtBytes(r.file.size)}</span>
            <span className={`sel-badge is-${r.status}`}><Icon name={STATUS_ICON[r.status]} size={11} />{r.label ?? <>{STATUS_LABEL[r.status]}{r.reason && <span className="sel-reason"> · {r.reason}</span>}</>}</span>
          </li>)}
        </ul>
        : <p className="muted sm fit-empty" role="status">No files match</p>}
      {n.failed > 0 && <p className="muted xs sel-hint">Failed files are never uploaded. Convert documents you need to {TYPES_LABEL} (for example, save as PDF) and pick them again.</p>}
    </section>
  )
}

/** Staged review: what was filtered out, what is usable, and a choice that must fit the batch before Upload turns on. */
function ReviewDialog({ review, existing, onClose, onUpload }: { review?: Review; existing: Existing; onClose: () => void; onUpload: (files: File[]) => void }) {
  const t = review?.t
  const theme = useUi(s => s.theme)
  const inReview = useUppyState(reviewUppy, s => s.files)
  // New selection: preselect everything if it fits single or multi-batch.
  useEffect(() => { if (t) setReviewFiles(fit(t.usable, existing, { allowMultiBatch: true }).ok ? t.usable : []) }, [review])
  if (!t || !review) return <Dialog open={false} title="" onClose={onClose} footer={null}>{null}</Dialog>
  const picked = Object.values(inReview).map(x => x.data as File)
  const f = fit(picked, existing, { allowMultiBatch: true })
  const rows = selectionRows(review), n = countBy(rows)
  const maxRoomFiles = f.multiBatch ? MAX_MULTI_BATCH_FILES : Math.max(f.roomFiles, 1)
  const maxRoomBytes = f.multiBatch ? MAX_MULTI_BATCH_BYTES : Math.max(f.roomBytes, 1)
  const pctFiles = Math.min(100, (f.count / maxRoomFiles) * 100), pctBytes = Math.min(100, (f.bytes / maxRoomBytes) * 100)
  const over = (n: number, room: number) => (n > room ? 'over' : n ? 'ok' : '')
  return (
    <Dialog open title="Review your selection" onClose={onClose} className="dialog wide review"
      footer={<>
        <span className={`review-reason ${f.ok ? 'tone-ok' : ''}`}>
          <Icon name={f.ok ? 'check' : 'lock'} size={12} />
          {f.ok
            ? (f.multiBatch ? 'Ready to upload.' : 'Fits the batch. Ready to upload.')
            : f.reason}
        </span>
        <button className="btn btn-secondary" onClick={() => { reviewUppy.clear(); onClose() }}>Cancel</button>
        <button className={`btn ${f.ok ? 'btn-ok' : 'btn-primary'}`} disabled={!f.ok} onClick={() => { const files = picked; reviewUppy.clear(); onUpload(files) }}>
          <Icon name="upload" />
          {f.count ? `Upload ${plural(f.count, 'file')}` : 'Upload'}
        </button>
      </>}>
      <ol className="funnel" aria-label="Selection funnel">
        <li><span className="funnel-n">{t.total.toLocaleString()}</span><span className="funnel-l">selected</span></li>
        <li className={t.usable.length ? 'is-ok' : 'is-err'}><span className="funnel-n">{t.usable.length.toLocaleString()}</span><span className="funnel-l">usable</span>{n.failed + n.filtered > 0 && <span className="funnel-sub">{[n.failed && `${n.failed.toLocaleString()} failed`, n.filtered && `${n.filtered.toLocaleString()} filtered out`].filter(Boolean).join(' · ')}</span>}</li>
        <li className={f.ok ? 'is-ok' : f.count ? 'is-err' : ''}><span className="funnel-n">{f.count}</span><span className="funnel-l">{f.multiBatch ? 'chosen' : 'chosen for this batch'}</span></li>
      </ol>
      {f.multiBatch && (
        <div className="multi-batch-card" style={{ margin: '8px 0 12px 0' }}>
          <p className="sm" style={{ margin: '4px 0 0 0', color: 'var(--text-sub)' }}>
            You chose <strong>{plural(f.count, 'file')}</strong> ({fmtBytes(f.bytes)}). One batch holds up to {MAX_BATCH_FILES} files, so they upload in <strong>{plural(f.batchCount ?? 1, 'batch', 'batches')}</strong>, one after another. Each file is checked as it arrives.
          </p>
        </div>
      )}
      <p className="review-lead">
        {n.usable === 0 ? <>None of the {plural(rows.length, 'file')} can be uploaded. Accepted types: {TYPES_LABEL}.</>
          : <>Out of <strong>{plural(rows.length, 'file')}</strong>, <strong>{n.usable.toLocaleString()}</strong> {n.usable === 1 ? 'is' : 'are'} usable.{' '}
            {fit(t.usable, existing, { allowMultiBatch: true }).ok
              ? (f.multiBatch ? 'All of them can be uploaded.' : 'All of them fit this batch.')
              : `Choose the important ones: up to ${plural(Math.max(f.roomFiles, 0), 'file')} and ${MB(Math.max(f.roomBytes, 0))}.`}</>}
      </p>
      {t.usable.length > 0 && <>
        <div className="meters">
          <div className={`meter ${over(f.count, maxRoomFiles)}`}>
            <span className="meter-l">Files</span>
            <span className="meter-bar"><span style={{ width: `${pctFiles}%` }} /></span>
            <span className="meter-n">{f.count} / {f.multiBatch ? `${MAX_MULTI_BATCH_FILES} (up to 9 batches)` : Math.max(f.roomFiles, 0)}</span>
          </div>
          <div className={`meter ${over(f.bytes, maxRoomBytes)}`}>
            <span className="meter-l">Size</span>
            <span className="meter-bar"><span style={{ width: `${pctBytes}%` }} /></span>
            <span className="meter-n">{fmtBytes(f.bytes)} / {f.multiBatch ? `${fmtBytes(MAX_MULTI_BATCH_BYTES)} (up to 9 batches)` : MB(Math.max(f.roomBytes, 0))}</span>
          </div>
        </div>
        <div className="tile-panel">
          <div className="tile-bar">
            <button className="link sm" onClick={() => setReviewFiles([])}>Clear</button>
            <strong className="tile-bar-title">{f.count} of {plural(Math.max(f.count, t.usable.length), 'file')} selected</strong>
            <span className="row">
              <button className="link sm" onClick={() => setReviewFiles(t.usable)}>Select all</button>
              <button className="link sm" onClick={() => setReviewFiles(autoPick(t.usable, existing))}><Icon name="bolt" size={12} />Auto-select what fits</button>
            </span>
          </div>
          {picked.length
            ? <FitGrid files={inReview} />
            : <Dashboard uppy={reviewUppy} className="bam-uppy" width="100%" height={340} theme={theme} hideUploadButton disableStatusBar
              hideProgressDetails proudlyDisplayPoweredByUppy={false} showSelectedFiles fileManagerSelectionType="both"
              note={`${TYPES_LABEL} · remove a file to leave it out of this batch`} />}
          {t.usable.length > LIST_CAP && <p className="muted xs tile-more">Select all takes the first {LIST_CAP} of {t.usable.length.toLocaleString()} usable files. Use Auto-select, or pick a smaller folder.</p>}
        </div>
        {t.usable.some(x => x.size > CHUNK_BYTES) && <details className="excluded chunk-branches">
          <summary><Icon name="upload" size={12} /> Large files upload in parts</summary>
          {t.usable.filter(x => x.size > CHUNK_BYTES).slice(0, 20).map(file => {
            const parts = chunkNames(file.name, file.size)
            return <div key={`${file.name}-${file.size}`} className="branch-file">
              <span className="branch-root"><Icon name="file" size={12} />{file.name}<span className="muted">{fmtBytes(file.size)}</span></span>
              <ul className="branch" aria-label={`${file.name} parts`}>
                {(parts.length > 4 ? [0, 1, 2, -1, parts.length - 1] : parts.map((_, i) => i)).map(i =>
                  <li key={i}>{i < 0 ? <span className="muted">… {parts.length - 4} more</span>
                    : <><span>Part {i + 1}</span><span className="muted">{fmtBytes(Math.min(CHUNK_BYTES, file.size - i * CHUNK_BYTES))}</span></>}</li>)}
              </ul>
            </div>
          })}
        </details>}
      </>}
      <SelectionList rows={rows} />
    </Dialog>
  )
}

const FLOW_STAGES = [
  { step: 1, label: 'Upload', icon: 'upload', desc: 'Flow 1/6: Streaming discovery files to air-gapped quarantine perimeter' },
  { step: 2, label: 'Quarantine', icon: 'lock', desc: 'Flow 2/6: Air-gapped quarantine staging & SHA-256 fingerprinting' },
  { step: 3, label: `${AV_NAME} (ClamAV)`, icon: 'shield', desc: 'Flow 3/6: ClamAV static MD5/SHA256 signature scan & macro AST inspection' },
  { step: 4, label: `${REPEL_NAME}`, icon: 'shield', desc: 'Flow 4/6: Repel behavioral payload repulsion & prompt injection defense' },
  { step: 5, label: 'Twin Verdict', icon: 'check', desc: 'Flow 5/6: Unanimous fail-closed consensus evaluation' },
  { step: 6, label: 'Released', icon: 'bolt', desc: 'Flow 6/6: Twin-engine certified files admitted to BAM RAG pipeline' },
]

/** Live security pipeline for the batch: upload, quarantine, BuLL*Sav scan, verdict, release. Every count comes from real state. */
function AvStatus({ uploads }: { uploads: UploadRecord[] }) {
  const files = useUppyState(uppy, s => s.files)
  const list = Object.values(files)
  const sending = list.filter(f => f.progress.uploadStarted && !f.progress.uploadComplete && !f.error)
  const failed = list.filter(f => f.error)
  const sendTotal = sending.reduce((n, f) => n + (f.size ?? 0), 0)
  const chunked = sending.filter(f => chunkCount(f.size ?? 0) > 1)
  const sendPct = sendTotal ? Math.round(sending.reduce((n, f) => n + (f.progress.bytesUploaded || 0), 0) / sendTotal * 100) : 0
  const total = uploads.length
  const pending = uploads.filter(u => u.scan === 'scanning' && !u.scanError)
  const inScan = pending.filter(u => u.scanStartedAt)
  const queued = pending.length - inScan.length
  const blocked = uploads.filter(u => u.scan === 'blocked').length
  const errors = uploads.filter(u => u.scanError).length
  const clean = uploads.filter(u => u.scan === 'verified').length
  const released = uploads.filter(u => u.released).length
  const state = sending.length || pending.length ? 'scanning' : !total ? 'idle' : blocked ? 'threat' : errors ? 'error' : 'clear'
  const s = (n: number) => (n === 1 ? '' : 's')

  const [animatingStep, setAnimatingStep] = useState<number | null>(null)
  const animationTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevUploadsCount = useRef(uploads.length)

  const runSequentialFlow = (targetStep = 1) => {
    if (animationTimer.current) clearTimeout(animationTimer.current)
    if (targetStep <= 6) {
      setAnimatingStep(targetStep)
      animationTimer.current = setTimeout(() => {
        runSequentialFlow(targetStep + 1)
      }, targetStep === 1 ? 650 : targetStep === 2 ? 750 : targetStep === 3 ? 850 : targetStep === 4 ? 800 : targetStep === 5 ? 700 : 800)
    } else {
      setAnimatingStep(null)
    }
  }

  // Auto-trigger sequential animation when batch is loaded or uploads start
  useEffect(() => {
    if (uploads.length > 0 && prevUploadsCount.current === 0) {
      runSequentialFlow(1)
    }
    prevUploadsCount.current = uploads.length
    return () => {
      if (animationTimer.current) clearTimeout(animationTimer.current)
    }
  }, [uploads.length])

  // Expose runSequentialFlow globally for external triggers
  useEffect(() => {
    ;(window as any).__triggerAvFlow = () => runSequentialFlow(1)
    return () => { delete (window as any).__triggerAvFlow }
  }, [])

  const isSimulating = animatingStep !== null
  const currentStepInfo = isSimulating ? FLOW_STAGES[animatingStep - 1] : null

  const [title, sub] = isSimulating
    ? [
        `Flow ${animatingStep}/6 Running: ${currentStepInfo?.label}`,
        currentStepInfo?.desc ?? '',
      ]
    : {
        idle: [`Dual Engines Ready: ${AV_NAME} & ${REPEL_NAME}`, 'Every upload is quarantined and evaluated by ClamAV and Repel before entering the BAM pipeline.'],
        scanning: sending.length && !pending.length
          ? [`Uploading ${sending.length} file${s(sending.length)} to quarantine · ${sendPct}%`, chunked.length ? `${chunked.length} large file${s(chunked.length)} sent in ${MB(CHUNK_BYTES)} chunks, 3 files at a time. Each file is scanned as soon as it lands.` : 'Up to 3 files at a time. Evaluated by ClamAV and Repel as soon as it lands in quarantine.']
          : [`Twin-Engine Defense Running · ${total - pending.length} of ${total} inspected`, 'ClamAV signature inspection + Repel behavioral evasion check active. Files stay quarantined until both pass.'],
        threat: [`${blocked} threat${s(blocked)} quarantined by security perimeter`, 'Blocked files cannot be opened or ingested. Remove them to continue.'],
        error: [`${errors} scan${s(errors)} did not finish`, 'No verdict means not clean. Retry the scan or remove the file.'],
        clear: [`All ${total} file${s(total)} clean (ClamAV & Repel Verified)`, 'Twin-engine security verified. Released to the BAM pipeline for ingestion.'],
      }[state]

  type St = 'idle' | 'active' | 'done' | 'alert'
  const resolved = total > 0 && !pending.length

  const nodes: { label: string; st: St; count: string; icon: string }[] = isSimulating
    ? FLOW_STAGES.map((fs, idx) => {
        const stepNum = idx + 1
        const st: St = stepNum < animatingStep ? 'done' : stepNum === animatingStep ? 'active' : 'idle'
        const count = stepNum < animatingStep ? 'Passed' : stepNum === animatingStep ? 'Running…' : 'Queued'
        return { label: fs.label, icon: fs.icon, st, count }
      })
    : [
        { label: 'Upload', icon: 'upload', st: sending.length ? 'active' : failed.length ? 'alert' : total ? 'done' : 'idle',
          count: sending.length ? `${sendPct}%` : failed.length ? `${failed.length} failed` : total ? `${total} sent` : '—' },
        { label: 'Quarantine', icon: 'lock', st: queued ? 'active' : total ? 'done' : 'idle', count: queued ? `${queued} waiting` : total ? `${total} held` : '—' },
        { label: `${AV_NAME} (ClamAV)`, icon: 'shield', st: inScan.length ? 'active' : errors ? 'alert' : resolved ? 'done' : 'idle', count: total ? `${total - pending.length}/${total}` : '—' },
        { label: `${REPEL_NAME}`, icon: 'shield', st: inScan.length ? 'active' : errors ? 'alert' : resolved ? 'done' : 'idle', count: total ? `${total - pending.length}/${total}` : '—' },
        { label: 'Twin Verdict', icon: blocked ? 'alert' : 'check', st: blocked || errors ? 'alert' : resolved ? 'done' : clean ? 'active' : 'idle',
          count: total ? `${clean} clean${blocked ? ` · ${blocked} blocked` : ''}` : '—' },
        { label: 'Released', icon: 'bolt', st: released && released === total ? 'done' : released ? 'active' : 'idle', count: total ? `${released} of ${total}` : '—' },
      ]

  const pct = isSimulating
    ? Math.round((animatingStep / 6) * 100)
    : sending.length && !pending.length
    ? sendPct
    : total
    ? Math.round(((total - pending.length) / total) * 100)
    : 0

  return (
    <section id="av-status" className={`av av-${isSimulating ? 'scanning' : state}`} aria-label="Virus scan status">
      <div className="av-head" role="status" aria-live="polite">
        <span className="av-radar" aria-hidden="true"><Icon name={isSimulating ? 'spinner' : state === 'threat' ? 'alert' : 'shield'} size={16} spin={isSimulating} /></span>
        <span className="av-text">
          <div className="row" style={{ alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
            <strong>{title}</strong>
            <button
              type="button"
              className="btn btn-secondary sm"
              style={{ fontSize: '11px', padding: '2px 8px', height: '24px' }}
              onClick={() => runSequentialFlow(1)}
              title="Re-run animated 6-flow defense progression"
            >
              <Icon name="refresh" size={10} spin={isSimulating} />
              <span>{isSimulating ? `Flow ${animatingStep}/6…` : 'Animate Defense Flow'}</span>
            </button>
          </div>
          <span>{sub}</span>
        </span>
      </div>
      <ol className="av-flow">
        {nodes.map(n => (
          <li key={n.label} className={`av-node is-${n.st}`}>
            <span className="av-dot"><Icon name={n.st === 'active' ? 'spinner' : n.icon} size={12} spin={n.st === 'active'} /></span>
            <span className="av-label">{n.label}</span>
            <span className="av-count">{n.count}</span>
          </li>
        ))}
      </ol>
      {(total > 0 || sending.length > 0 || isSimulating) && (
        <span className="av-bar" aria-hidden="true"><span style={{ width: `${pct}%` }} /></span>
      )}
      {chunked.length > 0 && <p className="av-now xs"><Icon name="upload" size={11} /> Chunked upload:
        {chunked.slice(0, 3).map(f => { const n = chunkCount(f.size ?? 0); const done = Math.min(n, Math.floor((f.progress.bytesUploaded || 0) / CHUNK_BYTES)); return <span key={f.id} className="mono">part {Math.min(n, done + 1)}/{n} · {f.name}</span> })}</p>}
      {inScan.length > 0 && !isSimulating && <p className="av-now xs"><Icon name="eye" size={11} /> Now scanning:
        {inScan.slice(0, 3).map(u => <span key={u.id} className="mono">{u.name}</span>)}{queued > 0 && <span className="muted">+{queued} waiting in quarantine</span>}</p>}
      {failed.length > 0 && <div className="av-failed">
        <span className="stage-error"><Icon name="alert" size={12} /> {failed.length} upload{s(failed.length)} failed and never reached quarantine: {failed.slice(0, 3).map(f => f.name).join(', ')}{failed.length > 3 ? ` and ${failed.length - 3} more` : ''}.</span>
        <span className="row"><button className="btn btn-secondary sm" onClick={() => void uppy.retryAll()}><Icon name="refresh" />Retry</button>
          <button className="btn btn-ghost sm" onClick={() => uppy.removeFiles(failed.map(f => f.id))}>Dismiss</button></span>
      </div>}
    </section>
  )
}

const EVENT: Record<ScanEvent['kind'], { icon: string; tone: string; text: string }> = {
  staged: { icon: 'upload', tone: 'muted', text: 'staged in quarantine' },
  started: { icon: 'clock', tone: 'muted', text: 'scan started' },
  clean: { icon: 'check', tone: 'tone-ok', text: 'clean, released' },
  threat: { icon: 'x', tone: 'tone-err', text: 'threat found, quarantined' },
  removed: { icon: 'minus', tone: 'muted', text: 'removed from batch' },
  error: { icon: 'alert', tone: 'tone-warn', text: 'scan error (not a threat)' },
}

function ScanActivity({ events }: { events: ScanEvent[] }) {
  return (
    <section className="rag-card" aria-labelledby="scan-act-title">
      <header className="rag-card-head"><h3 id="scan-act-title">Scan activity</h3></header>
      {!events.length ? <p className="muted sm">No scan events yet.</p> : (
        <ul className="scan-log">
          {[...events].sort((a, b) => b.at.localeCompare(a.at)).map(e => {
            const k = EVENT[e.kind]
            return (
              <li key={e.id} className={k.tone}>
                <Icon name={k.icon} size={12} label={e.kind === 'error' ? 'Error' : undefined} />
                <span className="scan-log-text">{e.name} — {k.text}</span>
                <Time iso={e.at} />
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function scanRow(snap: Snapshot) {
  const u = snap.uploads
  const scanning = u.filter(x => x.scan === 'scanning' && !x.scanError).length
  const errors = u.filter(x => x.scanError).length
  const blocked = u.filter(x => x.scan === 'blocked' || x.clamavVerdict === 'threat' || x.repelVerdict === 'threat').length
  const st = !u.length ? { cls: 'q', icon: 'circle', text: 'Queued', detail: 'waiting for uploads' }
    : scanning ? { cls: 'r', icon: 'spinner', text: 'Running', detail: `${scanning} scanning` }
    : errors ? { cls: 'f', icon: 'alert', text: 'Scan error', detail: `${errors} scan error${errors === 1 ? '' : 's'}` }
    : blocked ? { cls: 'f', icon: 'x', text: 'Blocked', detail: `${blocked} blocked` }
    : { cls: 'd', icon: 'check', text: 'Done', detail: `${u.length} clean (Dual engines)` }
  return (
    <li className={`pipe-step ps-${st.cls}`}>
      <span className="pipe-icon"><Icon name={st.icon} size={13} spin={st.cls === 'r'} /><span className="sr-only">{st.text}</span></span>
      <span className="pipe-label">Dual Scan ({AV_NAME} + {REPEL_NAME})</span>
      <span className="pipe-detail">{st.detail}</span>
    </li>
  )
}

/** Upload (file transfer) and Scan (security check), one component so both screens share state. */
export function Intake({ snap, info, view }: StepProps & { view: 'upload' | 'scan' }) {
  const { busy, run } = useAction()
  const [outcome, setOutcome] = useState<ScanOutcome>('clean')
  const closed = info.editable ? undefined : 'Uploads are closed while the SOW is approved. Reopen Scope to add documents.'
  const [details, setDetails] = useState(false)

  // Staged intake: triage the selection; upload straight away only when nothing was filtered and it all fits.
  const uppyFiles = useUppyState(uppy, s => s.files)
  const [review, setReview] = useState<Review>()
  const sendingFiles = Object.values(uppyFiles).filter(f => !f.progress.uploadComplete && !f.error)
  const existing = { count: snap.uploads.length + sendingFiles.length, bytes: snap.uploads.reduce((n, u) => n + u.size, 0) + sendingFiles.reduce((n, f) => n + (f.size ?? 0), 0) }
  const send = (files: File[]) => {
    setReview(undefined)
    if (files.length > MAX_BATCH_FILES) {
      // Smart multi-batch waves: one batch per MAX_BATCH_FILES files.
      const batchCount = Math.ceil(files.length / MAX_BATCH_FILES)
      for (let i = 0; i < batchCount; i++) uploadFiles(files.slice(i * MAX_BATCH_FILES, (i + 1) * MAX_BATCH_FILES), `batch-${i + 1}`)
    } else uploadFiles(files)
  }
  const take = (list: FileList, source: Source) => {
    const all = triage(list)
    // A file already in this intake (same name and size) is skipped, not uploaded twice.
    const have = new Set([...snap.uploads.map(u => `${u.name}|${u.size}`), ...Object.values(uppyFiles).map(f => `${f.name}|${f.size}`)])
    const dupes = all.usable.filter(f => have.has(`${f.name}|${f.size}`))
    const tr = dupes.length ? { ...all, usable: all.usable.filter(f => !dupes.includes(f)) } : all
    if (dupes.length) { const at = new Date().toISOString(); setUi(s => ({ rejected: [...s.rejected, ...dupes.map(f => ({ name: f.name, reason: 'Skipped - duplicate', at }))] })) }
    const filtered = tr.unsupported.length + tr.empty.length + tr.tooLarge.length + tr.system.length
    if (filtered) {
      // Each refused file by name with its reason (shown in Upload's Rejected list); a huge pick is summarised after 50.
      const at = new Date().toISOString()
      const named = [
        ...tr.unsupported.map(f => ({ name: f.name, reason: `Unsupported type (${extOf(f.name)})` })),
        ...tr.empty.map(f => ({ name: f.name, reason: 'Empty file' })),
        ...tr.tooLarge.map(f => ({ name: f.name, reason: `Over ${MB(MAX_UPLOAD_BYTES)}` })),
        ...tr.system.map(f => ({ name: f.name, reason: SYSTEM_FILE_LABEL })),
      ]
      const shown = named.slice(0, 50).map(r => ({ ...r, at }))
      if (named.length > 50) shown.push({ name: `${(named.length - 50).toLocaleString()} more files`, reason: 'Filtered out at selection (unsupported, empty, too large or system files)', at })
      setUi(s => ({ rejected: [...s.rejected, ...shown] }))
    }
    // Every source (files, folder, ZIP, bulk, drop) opens the review dialog: nothing uploads without it (per user, 15 Oct 2026).
    setReview({ source, t: tr, dupes })
  }
  const demo = demoControls() && api.demo
  // + Add files (left), the left-pane drop and the middle drop zone all arrive here (UploadPanes.tsx).
  const takeRef = useRef(take)
  takeRef.current = take
  useEffect(() => { if (view !== 'upload' || closed) return; setPickHandler((l, src) => takeRef.current(l, src)); return () => setPickHandler(null) }, [view, closed])
  // "Add files" from Review (1.4) lands here: Upload is open and the handler above is registered, so the picker opens at once.
  const armPicker = useUi(s => s.armPicker)
  useEffect(() => { if (view === 'upload' && !closed && armPicker) { setUi({ armPicker: undefined }); openPicker(armPicker) } }, [view, closed, armPicker])
  const docs = snap.uploads.length
  const failedUploads = Object.values(uppyFiles).filter(f => f.error)
  const dialogs = <>
    <ReviewDialog review={review} existing={existing} onClose={() => setReview(undefined)} onUpload={send} />
  </>
  const scanDetails = (
    <SecurityDetails snap={snap}>
      <section className="dc-sec"><h3><Icon name="scan" size={14} />Scan activity</h3>
        <AvStatus uploads={snap.uploads} />
        <PipelineCard title="Hand-off to RAG" lead={scanRow(snap)} />
        <ScanActivity events={snap.scanEvents} />
        {demo && <div className="row">
          <label className="pipe-sim">Simulate scan outcome
            <select value={outcome} onChange={e => { const o = e.target.value as ScanOutcome; setOutcome(o); demo.setScanOutcome(o); (window as any).__triggerAvFlow?.() }}>
              <option value="clean">None (clean)</option><option value="threat">Threat detected</option><option value="error">Scanner outage</option>
            </select>
          </label>
          <button className="btn btn-ghost sm" disabled={!!snap.ingestion} onClick={() => { demo.loadScanFixture(); (window as any).__triggerAvFlow?.(); void run('Load fixture', async () => {}) }}>Load mockup batch</button>
        </div>}
      </section>
    </SecurityDetails>
  )

  if (view === 'upload') {
    // FDE view: results only (files on the left, conversation in the middle; UploadPanes.tsx). The scan and quarantine
    // run inside Upload and still gate everything downstream; their diagnostics appear only with ?demo=1.
    return <>
      <UploadResults snap={snap} />
      {demo && <p className="up-demo"><button className="link xs" onClick={() => setDetails(true)}>Upload diagnostics (demo)</button></p>}
      {demo && <Dialog open={details} title="Upload diagnostics" onClose={() => setDetails(false)} className="dialog wide dc-details" footer={<button className="btn btn-secondary" onClick={() => setDetails(false)}>Close</button>}>{scanDetails}</Dialog>}
      {dialogs}
    </>
  }

  const scanning = snap.uploads.filter(u => u.scan === 'scanning' && !u.scanError).length
  const clean = snap.uploads.filter(u => u.scan === 'verified').length
  const held = snap.uploads.filter(u => u.scan === 'blocked').length
  const errors = snap.uploads.filter(u => u.scanError)
  const retryable = errors.length + failedUploads.length
  const retryAll = () => { if (failedUploads.length) void uppy.retryAll(); for (const u of errors) void run('Retry scan', () => api.retryScan(u.id), { step: 1 }) }
  const engine = (k: 'clamavVerdict' | 'repelVerdict') => snap.uploads.filter(u => u[k] === 'clean').length
  const st = !docs ? { i: 'scan', t: 'Nothing to scan yet', pill: '', c: '' }
    : scanning ? { i: 'spinner', t: 'Scanning', pill: 'In progress', c: 'tone-info' }
    : held ? { i: 'alert', t: `${held} file${held === 1 ? '' : 's'} held`, pill: 'Held', c: 'tone-err' }
    : errors.length ? { i: 'alert', t: 'Scan errors', pill: 'Retry', c: 'tone-warn' }
    : { i: 'shield', t: 'All files clean', pill: 'Passed', c: 'tone-ok' }
  return (
    <main className="ing dc" aria-labelledby="center-title">
      <ScreenTitle title="Scan" help="Dual-engine virus scan per file" details={scanDetails} detailsOpen={details} setDetailsOpen={setDetails}>
        {retryable > 0 && <button className="btn btn-outline" disabled={!info.editable || !!busy} onClick={retryAll}><Icon name="refresh" />Retry failed</button>}
      </ScreenTitle>
      <section className="dc-status" aria-live="polite">
        <span className={`dc-status-icon ${st.c}`}><Icon name={st.i} size={18} spin={st.i === 'spinner'} /></span>
        <div className="dc-status-text">
          <strong>{st.t}</strong>
          <span className="muted xs">{docs ? `${clean} clean · ${held} infected · ${scanning} scanning · ClamAV ${engine('clamavVerdict')}/${docs} · Repel ${engine('repelVerdict')}/${docs}` : 'Add documents in Upload; each one is scanned automatically.'}</span>
        </div>
        {st.pill && <span className={`dc-pill ${st.c}`}>{st.pill}</span>}
      </section>
      {docs > 0 && <ScanTable uploads={snap.uploads} editable={info.editable} />}
      {docs > 0 && <button className="dc-pointer" onClick={() => setDetails(true)}><Icon name="info" size={13} />Scan activity and security details<Icon name="chevron" size={12} /></button>}
    </main>
  )
}
