// Ingestion declutter (14 Oct 2026, BAM Ingestion declutter wireframes): pieces shared by every Ingestion screen and
// the RAG build that follows. One primary action per screen, pinned in the Results & edits footer (IngestFooter /
// RagFooter); everything secondary sits behind the details icon (IngestDetails). Handlers, API calls and gates are
// the existing ones, only moved: startIngestionRun + ragPipeline.start, ingestionBlock, the context judge.
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { useUppyState } from '@uppy/react'
import { api } from '../api/adapter'
import { AV_NAME, AV_VENDOR, type Snapshot, type UploadRecord } from '../api/types'
import { ingestionBlock, isReleased, scanView } from '../api/scanGate'
import { useAction } from '../lib/data'
import { browsedHere, goScreen } from '../lib/route'
import { setUi, uppy } from '../lib/store'
import { DEMO_RUN } from '../lib/demo'
import { isRunning, ragPipeline, useRag } from '../rag/ragPipeline'
import { Dialog, GateButton, Icon, StatusPill, Time, fmtBytes } from '../components/ui'

// ---- auto-advance (per user, 15 Oct 2026): Ingestion moves on by itself; Review's Continue to Sync is the human gate.
/** Runs `action` when `ready` turns true while this screen is open. Already true on arrival (the user came back on
 *  purpose) never moves the view, and the footer button stays as the manual path. */
/** Runs `action` when `ready` turns true on this screen, or right away when the flow arrives with the work already
 *  done (never when the person browsed here: no bounce). */
export function useAdvanceWhen(ready: boolean, action: () => void) {
  const was = useRef(ready && browsedHere())
  useEffect(() => { if (ready && !was.current) action(); was.current = ready }, [ready])
}

// ---- the four intake checks (Document checklist, assistant message, footer) -------------------------------
export interface Check { label: string; state: 'ready' | 'pending' | 'next' | 'fail'; detail: string }
export function intakeChecks(snap: Snapshot, inFlight: number): Check[] {
  const u = snap.uploads
  const scanning = u.filter(x => x.scan === 'scanning' && !x.scanError).length
  const held = u.filter(x => x.scan === 'blocked').length
  const errors = u.filter(x => x.scanError).length
  const accepted = u.filter(isReleased).length
  return [
    { label: 'Uploads finished', state: !u.length ? 'pending' : inFlight ? 'pending' : 'ready', detail: !u.length ? 'No documents yet' : inFlight ? `${inFlight} still uploading` : 'All uploads finished' },
    { label: 'Scan passed', state: !u.length || scanning ? 'pending' : held || errors ? 'fail' : 'ready', detail: `${accepted} accepted · ${scanning} pending scan${held ? ` · ${held} held` : ''}${errors ? ` · ${errors} scan errors` : ''}` },
    { label: 'Handed to the knowledge build', state: snap.ingestion ? 'ready' : 'next', detail: snap.ingestion ? `Run ${snap.ingestion.runId}` : 'Starts with Start in Upload' },
  ]
}

// ---- Start ingestion: the existing hand-off, gated by the existing rule plus the sufficiency check --------
export function useIngestStart(snap: Snapshot) {
  const rag = useRag(s => s)
  const files = Object.values(useUppyState(uppy, s => s.files))
  const inFlight = files.filter(f => f.progress.uploadStarted && !f.progress.uploadComplete && !f.error).length
  const uploadErrors = files.filter(f => f.error).length
  const { busy, run } = useAction()
  const startingRef = useRef(false)
  const starting = busy === 'Start ingestion'
  const editable = snap.steps[0]!.editable
  const block = !editable ? 'Reopen Scope to change the intake batch'
    : uploadErrors ? 'Retry or dismiss failed uploads'
    : ingestionBlock(snap.uploads, { uploading: inFlight, starting, running: isRunning(rag), ingestion: snap.ingestion })
  const start = async (fileIds?: string[]) => {
    if (startingRef.current) return // double-click guard; the backend also rejects a repeated start
    startingRef.current = true
    const r = await run('Start ingestion', () => api.startIngestionRun(fileIds), { step: 1 })
    startingRef.current = false
    if (!r) return
    // The backend starts the knowledge build for the released files with the run above; nothing else to start here.
    goScreen('1.9') // Parse shows the extraction that follows (user action, so moving the view is allowed)
  }
  return { start, block, starting, inFlight, uploadErrors }
}

/** The FDE never sees scan or quarantine terms (they run inside Upload): the same gate, said neutrally. */
const NEUTRAL: Record<string, string> = {
  'Retry or remove files whose scan failed': 'Retry the file check or remove the file in Upload',
  'Wait for virus scans to finish': 'Wait for the upload checks to finish',
  'Remove blocked files to continue': 'Remove the files that cannot be used in Upload',
  'Retry or dismiss failed uploads': 'Retry or dismiss failed uploads in Upload',
  'These verified files are already ingested': 'These files are already handed on',
  'An ingestion run is already running': 'The files are already being parsed',
  'Ingestion run is starting': 'Starting',
}
export const fdeReason = (reason: string) => NEUTRAL[reason] ?? reason

/** What the FDE sees for a stored upload: readiness and an actionable reason, never scan internals. */
export function fdeView(u: UploadRecord): { pill: 'pass' | 'running' | 'fail' | 'needs_review'; label: string; text: string } {
  if (u.status === 'failed') return { pill: 'fail', label: 'Needs attention', text: 'The upload failed. Remove it and upload it again.' }
  if (u.scanError) return { pill: 'needs_review', label: 'Needs attention', text: 'The file check did not finish. Retry the check or remove the file.' }
  if (u.scan === 'blocked' || u.clamavVerdict === 'threat') return { pill: 'fail', label: 'Needs attention', text: 'This file cannot be used. Remove it and upload a different copy.' }
  if (u.scan === 'scanning' || !isReleased(u)) return { pill: 'running', label: 'Checking', text: 'Checking the upload' }
  return { pill: 'pass', label: 'Ready', text: 'Ready to process' }
}

// ---- screen title row with the details icon ----------------------------------------------------------------
export function ScreenTitle({ title, help, children, details, detailsOpen, setDetailsOpen }: {
  title: string; help: ReactNode; children?: ReactNode; details?: ReactNode; detailsOpen?: boolean; setDetailsOpen?: (v: boolean) => void
}) {
  const [own, setOwn] = useState(false)
  const open = detailsOpen ?? own, setOpen = setDetailsOpen ?? setOwn
  return (
    <header className="dc-head">
      <div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>{title}</h2><p className="muted sm">{help}</p></div>
      <div className="dc-head-actions">
        {children}
        {details && <button className="icon-btn" aria-label="Details" title="Details" aria-haspopup="dialog" onClick={() => setOpen(true)}><Icon name="info" /></button>}
      </div>
      {details && <Dialog open={open} title="Details" onClose={() => setOpen(false)} className="dialog wide dc-details" footer={<button className="btn btn-secondary" onClick={() => setOpen(false)}>Close</button>}>{details}</Dialog>}
    </header>
  )
}

/** Security summary, simulation note and dataset provenance: available, but out of the main flow. */
export function SecurityDetails({ snap, children }: { snap: Snapshot; children?: ReactNode }) {
  const runId = useRag(s => s.runId)
  return <>
    <section className="dc-sec"><h3><Icon name="shield" size={14} />Security checks</h3>
      <ul className="muted sm">
        <li><strong>{AV_NAME}</strong> ({AV_VENDOR}, ClamAV engine): static signature and macro scan.</li>
        <li>A file that fails or cannot be scanned stays in quarantine and never reaches the knowledge build.</li>
      </ul>
    </section>
    {children}
  </>
}

// ---- compact file list: the first 5 rows, then "N more files" ------------------------------------------------
export function FileRows({ files, render }: { files: UploadRecord[]; render?: (u: UploadRecord) => ReactNode }) {
  const [all, setAll] = useState(false)
  const shown = all ? files : files.slice(0, 5)
  return (
    <ul className="dc-files">
      {shown.map(u => (
        <li key={u.id}>
          <Icon name="file" size={15} />
          <span className="dc-name">{u.name}</span>
          <span className="muted xs dc-size">{fmtBytes(u.size)}</span>
          {render ? render(u) : <FileMark u={u} />}
        </li>
      ))}
      {files.length > 5 && <li className="dc-more"><button className="link sm" aria-expanded={all} onClick={() => setAll(a => !a)}>
        <Icon name={all ? 'down' : 'chevron'} size={12} />{all ? 'Show fewer' : `${files.length - 5} more file${files.length - 5 === 1 ? '' : 's'}`}</button></li>}
    </ul>
  )
}
/** Status as an icon with its text for assistive tech (and a tooltip). */
export function FileMark({ u }: { u: UploadRecord }) {
  const v = fdeView(u)
  const s = v.pill === 'pass' ? { i: 'check', c: 'tone-ok', t: v.label } : v.pill === 'running' ? { i: 'spinner', c: 'tone-info', t: v.label } : { i: 'alert', c: 'tone-warn', t: v.label }
  return <span className={`dc-mark ${s.c}`} title={s.t}><Icon name={s.i} size={14} spin={s.i === 'spinner'} /><span className="sr-only">{s.t}</span></span>
}


// ---- Uploaded files: the live per-file scan table (file, object path, size, time, scan status, actions) ----------
type ScanFilter = 'all' | UploadRecord['scan']
export function ScanTable({ uploads, editable, fde }: { uploads: UploadRecord[]; editable: boolean; fde?: boolean }) {
  return fde ? <UploadTable uploads={uploads} editable={editable} /> : <ScanTableFull uploads={uploads} editable={editable} />
}
function ScanTableFull({ uploads, editable }: { uploads: UploadRecord[]; editable: boolean }) {
  const [filter, setFilter] = useState<ScanFilter>('all')
  const { busy, run } = useAction()
  const n = (st: ScanFilter) => uploads.filter(u => u.scan === st).length
  // Filtering is display only: eligibility always uses the full batch.
  const shown = filter === 'all' ? uploads : uploads.filter(u => u.scan === filter)
  const view = (u: UploadRecord) => run('View file', async () => { await api.getVerifiedFileAccess(u.id); setUi({ fileDetail: u.id }); goScreen('1.3') }, { step: 1 })
  return (
    <section className="scan-files" aria-labelledby="files-title">
      <header className="scan-files-head">
        <h3 id="files-title">Uploaded files <span className="muted xs">{uploads.length} file{uploads.length === 1 ? '' : 's'}</span></h3>
        <span className="scan-counts xs">
          <span className="muted">{n('scanning')} scanning</span><span className="tone-ok">{n('verified')} verified</span><span className="tone-err">{n('blocked')} blocked</span>
        </span>
        <label className="sr-only" htmlFor="scan-filter">Filter by scan status</label>
        <select id="scan-filter" className="select" value={filter} onChange={e => setFilter(e.target.value as ScanFilter)}>
          <option value="all">All statuses</option><option value="verified">Verified</option><option value="scanning">Scanning</option><option value="blocked">Blocked</option>
        </select>
      </header>
      {!shown.length ? <p className="muted sm">No files with this status.</p> : (
        <div className="scroll-box">
          <table className="table scan-table">
            <thead><tr><th scope="col">File name</th><th scope="col">Size</th><th scope="col">Uploaded time</th><th scope="col">Scan status</th><th scope="col" className="ta-r">Actions</th></tr></thead>
            <tbody>{shown.map(u => {
              const v = scanView(u)
              const can = (a: string) => editable && (v.actions as string[]).includes(a)
              const live = u.scan === 'scanning' && !u.scanError
              return (
                <tr key={u.id} className={u.scan === 'blocked' ? 'row-fail' : live ? 'row-scanning' : ''}>
                  <th scope="row"><span className="scan-file"><Icon name="file" />
                    <span className="file-main"><span className="file-name">{u.name}</span><span className="mono xs faint scan-path" title={u.objectKey}>{u.objectKey}</span></span></span></th>
                  <td>{fmtBytes(u.size)}</td>
                  <td><Time iso={u.uploadedAt} /></td>
                  <td><StatusPill status={v.pill} text={v.label} /><div className={`xs ${u.scan === 'blocked' ? 'tone-err' : 'muted'} scan-note`}>{v.text}</div>
                    {live && <span className="av-laser" aria-hidden="true" />}</td>
                  <td className="ta-r"><span className="scan-actions">
                    {(v.actions as string[]).includes('view') && <button className="link sm" disabled={!!busy} onClick={() => void view(u)}>View file</button>}
                    {can('retry') && <button className="btn btn-secondary sm" disabled={!!busy} onClick={() => run('Retry scan', () => api.retryScan(u.id), { step: 1 })}><Icon name="refresh" />Retry scan</button>}
                    {can('remove') && <button className={`btn ${u.scan === 'blocked' ? 'btn-danger' : 'btn-ghost'} sm`} disabled={!!busy} onClick={() => run('Remove file', () => api.removeUpload(u.id), { step: 1 })}>Remove</button>}
                    {!v.actions.length && <span className="faint xs">Unavailable</span>}
                  </span></td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
      )}
      <footer className="rag-card-foot">
        <span>{uploads.length} files · {n('verified')} verified · {n('scanning')} scanning · {n('blocked')} blocked</span>
      </footer>
    </section>
  )
}

/** Upload's file list for the FDE: readiness and actionable reasons only (the internal scan table stays for diagnostics). */
function UploadTable({ uploads, editable }: { uploads: UploadRecord[]; editable: boolean }) {
  const { busy, run } = useAction()
  const views = uploads.map(u => ({ u, v: fdeView(u) }))
  const ready = views.filter(x => x.v.pill === 'pass').length, attention = views.filter(x => x.v.pill === 'fail' || x.v.pill === 'needs_review').length
  return (
    <section className="scan-files" aria-labelledby="files-title">
      <header className="scan-files-head">
        <h3 id="files-title">Uploaded files <span className="muted xs">{uploads.length} file{uploads.length === 1 ? '' : 's'}</span></h3>
        <span className="scan-counts xs"><span className="tone-ok">{ready} ready</span>{attention > 0 && <span className="tone-warn">{attention} need attention</span>}</span>
      </header>
      <div className="scroll-box">
        <table className="table scan-table">
          <thead><tr><th scope="col">File name</th><th scope="col">Size</th><th scope="col">Uploaded time</th><th scope="col">Status</th><th scope="col" className="ta-r">Actions</th></tr></thead>
          <tbody>{views.map(({ u, v }) => (
            <tr key={u.id} className={v.pill === 'fail' ? 'row-fail' : ''}>
              <th scope="row"><span className="scan-file"><Icon name="file" /><span className="file-main"><span className="file-name">{u.name}</span></span></span></th>
              <td>{fmtBytes(u.size)}</td>
              <td><Time iso={u.uploadedAt} /></td>
              <td><StatusPill status={v.pill} text={v.label} /><div className={`xs ${v.pill === 'fail' ? 'tone-err' : 'muted'} scan-note`}>{v.text}</div></td>
              <td className="ta-r"><span className="scan-actions">
                {editable && u.scanError && <button className="btn btn-secondary sm" disabled={!!busy} onClick={() => run('Retry check', () => api.retryScan(u.id), { step: 1 })}><Icon name="refresh" />Retry check</button>}
                {editable && v.pill !== 'running' && <button className={`btn ${v.pill === 'fail' ? 'btn-danger' : 'btn-ghost'} sm`} disabled={!!busy} onClick={() => run('Remove file', () => api.removeUpload(u.id), { step: 1 })}>Remove</button>}
              </span></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  )
}
