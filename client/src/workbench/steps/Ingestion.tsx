// Ingestion screens besides Upload and Standardize: Parse, Review and the pinned footers, plus Connectors (opened from
// Review) and the internal diagnostics File detail and Quarantine (no FDE route). Built from the real service model
// only: stored upload records, the RAG run's extraction steps, and files refused at intake. Per-file parsing has no
// backend contract in this build, so Parse shows the run's extraction state, never invented per-file results.
import { useEffect, useRef, useState } from 'react'
import type { Snapshot } from '../api/types'
import { setUi, useUi } from '../lib/store'
import { goScreen } from '../lib/route'
import { useAction } from '../lib/data'
import { api } from '../api/adapter'
import { Copy, Empty, GateButton, Icon, Kpis, StatusPill, Time, Tip, fmtBytes } from '../components/ui'
import { tips } from '../lib/actionTips'
import { FileRows, ScreenTitle, SecurityDetails, fdeReason, fdeView, useAdvanceWhen, useIngestStart } from './IngestionFlow'
import { Foot, needsReview, useReviewFiles, useStandardize } from './Standardize'
import { STATE_TEXT } from '../api/standardize'
import { UploadProgress, isAdded, uploadCount, useUploadRows } from './UploadPanes'
import { ragPipeline, useRag } from '../rag/ragPipeline'
import StatusMark from '../components/reactbits/StatusMark'
import { ReviewWaiting } from './ReviewWaiting'
import { isReleased, scanView } from '../api/scanGate'
import { canAddFiles, useGoal } from '../lib/goal'

// ---- File detail/preview (opened from Scan) ------------------------------------------------------------------------
export function FileDetail({ snap }: { snap: Snapshot }) {
  const picked = useUi(s => s.fileDetail)
  const file = snap.uploads.find(u => u.id === picked) ?? snap.uploads[0]
  const doc = useRag(s => s.docs).find(d => file && d.title === file.name)
  const ext = file?.name.slice(file.name.lastIndexOf('.') + 1).toUpperCase()
  return (
    <div className="rag">
      <div className="rag-grid g13">
        <section className="rag-card" aria-labelledby="fl-title">
          <header className="rag-card-head"><h3 id="fl-title">Stored files</h3><span className="tag">{snap.uploads.length}</span></header>
          {snap.uploads.length === 0 ? <Empty icon="upload" title="No files yet" action={<button className="btn btn-primary" onClick={() => goScreen('1.1')}><Icon name="upload" />Upload documents</button>}>Stored files appear here.</Empty>
            : <ul className="nav-list">{snap.uploads.map(u => (
                <li key={u.id}><button className="nav-item" aria-current={file?.id === u.id || undefined} onClick={() => setUi({ fileDetail: u.id })}>
                  <span className="file-name">{u.name}</span><span className="muted xs">{fmtBytes(u.size)}</span></button></li>))}</ul>}
        </section>
        <div className="rag-col">
          {!file ? <section className="rag-card"><Empty icon="file" title="Select a file">Upload documents, then pick one to see its metadata and preview.</Empty></section> : <>
            <section className="rag-card" aria-labelledby="fd-title">
              <header className="rag-card-head"><h3 id="fd-title" className="file-name">{file.name}</h3><StatusPill status={file.status === 'stored' ? 'pass' : 'fail'} text={file.status === 'stored' ? 'Stored' : 'Failed'} /></header>
              <dl className="kv two-col">
                <div><dt>Type</dt><dd className="mono">{ext}</dd></div>
                <div><dt>Size</dt><dd>{fmtBytes(file.size)}</dd></div>
                <div><dt>Stored</dt><dd><Time iso={file.uploadedAt} /></dd></div>
                <div><dt>Object key</dt><dd><Copy value={file.objectKey} label="object key" /></dd></div>
                <div><dt>Upload ID</dt><dd><Copy value={file.id} label="upload ID" /></dd></div>
                {file.error && <div><dt>Error</dt><dd className="stage-error">{file.error}</dd></div>}
              </dl>
            </section>
            <section className="rag-card" aria-labelledby="pp-title">
              <header className="rag-card-head"><h3 id="pp-title">Parsed preview and flags</h3><StatusPill status="unavailable" text="Not available" /></header>
              <p className="muted sm">This file&apos;s rows, text and flags can&apos;t be shown yet. Nothing here is inferred or made up.</p>
              {doc ? (
                <div className="lineage">
                  <p className="sm"><strong>Knowledge coverage</strong></p>
                  <dl className="kv two-col">
                    <div><dt>Status</dt><dd>{doc.status.toLowerCase()}</dd></div><div><dt>Chunks</dt><dd>{doc.chunks}</dd></div>
                    <div><dt>Entities</dt><dd>{doc.entities}</dd></div>
                  </dl>
                </div>
              ) : <p className="muted xs">This file is not part of a knowledge version yet.</p>}
            </section>
          </>}
        </div>
      </div>
    </div>
  )
}

// ---- Parse ---------------------------------------------------------------------------------------------------
/** Extraction of the current run (the existing pipeline steps: chunk + embed, entity extraction). Per-file parsing has
 *  no backend contract, so every input file shows the run's extraction state; the documents of the version are in the details. */
export function ParseView({ snap }: { snap: Snapshot }) {
  const rag = useRag(s => s)
  const files = snap.uploads.filter(u => snap.ingestion?.fileIds.includes(u.id))
  const step = (k: string) => rag.steps.find(x => x.key === k)
  const ex = step('extract'), ch = step('chunk')
  const failed = [ch, ex].find(x => x?.status === 'failed')
  const state = !snap.ingestion ? 'waiting' : failed ? 'failed' : ex?.status === 'done' ? 'parsed' : 'parsing'
  const PILLS = { waiting: ['Waiting', ''], parsing: ['Parsing', 'tone-info'], parsed: ['Parsed', 'tone-ok'], failed: ['Failed', 'tone-err'] } as const
  return (
    <main className="ing dc" aria-labelledby="center-title">
      <ScreenTitle title="Extract text & structure" help="Uses the original, or its approved analysis copy from Standardize." />
      <Kpis label="Parse summary" items={[
        { value: files.length, label: 'Ready files' },
        { value: state === 'parsed' ? files.length : 0, label: 'Parsed', tone: state === 'parsed' ? 'ok' : undefined },
        { value: state === 'parsing' ? files.length : state === 'failed' ? `${files.length} failed` : 0, label: state === 'failed' ? 'Errors' : 'Parsing', tone: state === 'failed' ? 'err' : undefined },
      ]} />
      {!snap.ingestion ? <p className="dc-none"><Icon name="tb-transform" size={16} />Nothing to parse yet. Standardize hands the files on once every file is ready.</p> : (
        <ul className="dc-list" aria-label="Files">
          {files.map(u => <li key={u.id}><Icon name="tb-file-code" size={15} /><div><strong className="dc-name">{u.name}</strong>
            <span className="muted xs">{state === 'parsed' ? 'Text, tables and structure extracted' : state === 'failed' ? failed?.error ?? 'Extraction failed' : 'Extracting text, tables and structure'}</span></div>
            <span className={`dc-pill ${PILLS[state][1]}`}>{state === 'parsing' && <Icon name="spinner" size={11} spin />}{PILLS[state][0]}</span></li>)}
        </ul>
      )}
      {failed && <div className="rr-warn tone-err" role="alert"><Icon name="alert" size={15} /><div><strong className="sm">{failed.label} failed</strong><span className="xs">{failed.error ?? 'No reason reported'}</span></div>
        <button className="btn btn-secondary sm" onClick={() => ragPipeline.retry(failed.key)}><Icon name="refresh" />Retry</button></div>}
      <div className="dc-rows">
        <details><summary><strong>Extraction details</strong><span className="dc-count">{rag.docs.length}</span><Icon name="down" size={13} /></summary>
          <p className="muted sm">Extracted text and tables stay linked to their source documents.</p>
          <ul className="dc-files">{rag.docs.map(d => <li key={d.id}><Icon name="file" size={15} /><span className="dc-name">{d.title}</span><span className="muted xs">{d.chunks} chunks · {d.entities} entities</span><span className="muted xs">{d.reason ?? d.status.toLowerCase()}</span></li>)}</ul>
        </details>
      </div>
    </main>
  )
}

// ---- Review --------------------------------------------------------------------------------------------------
/** Confirms the accepted, parsed files that go to Context. Not SOW approval. Only eligible files are listed;
 *  everything else is one neutral "Excluded" row. */
export function IngestionReview({ snap }: { snap: Snapshot }) {
  const rejected = useUi(s => s.rejected)
  const rag = useRag(s => s)
  const std = useStandardize(snap)
  const ingested = snap.uploads.filter(u => snap.ingestion?.fileIds.includes(u.id))
  const ex = rag.steps.find(x => x.key === 'extract')
  const parsed = !!snap.ingestion && ex?.status === 'done', parseFailed = ex?.status === 'failed'
  const excluded = snap.uploads.length - ingested.length
  // Held for review = the shared rule (useReviewFiles), the same files Upload flags and the chat counts. Any other file
  // not handed on (e.g. its content was unavailable) is listed separately with its reason, so nothing is left out silently.
  const { files: held } = useReviewFiles(snap)
  const notHandedOn = snap.ingestion ? std.filter(f => !snap.ingestion!.fileIds.includes(f.u.id) && !needsReview(f)) : []
  const parseText = parsed ? 'Complete' : parseFailed ? 'Failed' : snap.ingestion ? 'Parsing' : 'Waiting'
  // Nothing handed on yet: the whole pane is the waiting state. The hand-off plays it out over the review (~450ms).
  const waiting = !handedOn(snap)
  const [morph, setMorph] = useState(false)
  const wasWaiting = useRef(waiting)
  useEffect(() => { if (wasWaiting.current && !waiting) setMorph(true); wasWaiting.current = waiting }, [waiting])
  useEffect(() => { if (!morph) return; const t = setTimeout(() => setMorph(false), 450); return () => clearTimeout(t) }, [morph])
  if (waiting) return (
    <main className="ing dc rw-main" aria-labelledby="center-title">
      <h2 id="center-title" className="sr-only" tabIndex={-1}>Review</h2>
      <ReviewWaiting />
    </main>
  )
  return (
    <main className={`ing dc${morph ? ' rw-enter' : ''}`} aria-labelledby="center-title">
      {morph && <ReviewWaiting leaving />}
      <ScreenTitle title="Review accepted files" help="Close Ingestion before opening Context. This is not SOW approval." />
      <Kpis label="Review summary" items={[
        { value: parsed ? ingested.length : 0, label: 'Accepted & parsed', tone: parsed ? 'ok' : undefined },
        { value: excluded + rejected.length, label: 'Excluded' },
        { value: parseFailed ? ingested.length : 0, label: 'Parse errors', tone: parseFailed ? 'err' : undefined },
      ]} />
      {!ingested.length ? <p className="dc-none"><Icon name="tb-clipboard-check" size={16} />No files handed on yet. Standardize hands the uploads on once they are ready.</p> : (
        <ul className="dc-list" aria-label="Accepted files">
          {ingested.map(u => {
            const f = std.find(x => x.u.id === u.id)
            return <li key={u.id}><StatusMark className="rb-sm" size={16} color="var(--accent)" doneColor="var(--ok)" errorColor="var(--err)"
                status={parsed ? 'done' : parseFailed ? 'failed' : snap.ingestion ? 'running' : 'pending'} /><div><strong className="dc-name">{u.name}</strong>
              <span className="muted xs">Standardize: {f?.review ? 'Reviewed' : 'Passed'} · Parse: {parseText}</span></div>
              <span className={`dc-pill ${parsed ? 'tone-ok' : parseFailed ? 'tone-err' : ''}`}>{parsed ? 'Accepted' : parseFailed ? 'Parse error' : 'Waiting'}</span></li>
          })}
        </ul>
      )}
      <section className="std-card">
        <h3>Handoff to Context</h3>
        <ul className="dc-list">
          <li><Icon name="check" size={15} /><div><strong>Accepted and parsed files</strong><span className="muted xs">Source links and analysis copies retained</span></div><span className={`dc-pill ${parsed ? 'tone-ok' : ''}`}>{parsed ? 'Ready' : 'Waiting'}</span></li>
          <li><Icon name="minus" size={15} /><div><strong>Excluded files</strong><span className="muted xs">Not sent to Context: removed, not ready, or refused at selection</span></div><span className="dc-pill">{excluded + rejected.length} excluded</span></li>
        </ul>
      </section>
      <div className="dc-rows">
        {held.length > 0 && <details open>
          <summary><strong>Held for review</strong><span className="dc-count">{held.length}</span><Icon name="down" size={13} /></summary>
          <ul className="dc-files">{held.map(f => <li key={f.u.id}><Icon name="alert" size={15} /><span className="dc-name">{f.u.name}</span></li>)}</ul>
        </details>}
        {notHandedOn.length > 0 && <details open>
          <summary><strong>Not handed on</strong><span className="dc-count">{notHandedOn.length}</span><Icon name="down" size={13} /></summary>
          <ul className="dc-files">{notHandedOn.map(f => <li key={f.u.id}><Icon name="file" size={15} /><span className="dc-name">{f.u.name}</span><span className="muted xs">{STATE_TEXT[f.a.state]}{f.a.reason ? `: ${f.a.reason}` : ''} · not sent to Context</span></li>)}</ul>
        </details>}
        {rejected.length > 0 && <details>
          <summary><strong>Refused at selection</strong><span className="dc-count">{rejected.length}</span><Icon name="down" size={13} /></summary>
          <ul className="dc-files">{rejected.map((r, i) => <li key={i}><Icon name="file" size={15} /><span className="dc-name">{r.name}</span><span className="muted xs">{r.reason}</span></li>)}</ul>
        </details>}
      </div>
    </main>
  )
}

// ---- pinned footers (one primary action each) ------------------------------------------------------------------
/** Upload's one action: Start sends the batch to the backend (one pipeline start: ingestion, then the knowledge
 *  build, then the SOW) and opens Parse. It waits for the click in the Ready state (no auto-advance from Upload). */
export function UploadFooter({ snap }: { snap: Snapshot }) {
  const all = useUploadRows(snap), rows = all.filter(isAdded)
  const progress = uploadCount(all)
  const busy = rows.some(r => r.state === 'uploading' || r.state === 'checking')
  const ready = rows.filter(r => r.state === 'ready').length
  // Locked until the goal is approved (approval gate): the footer says how to unlock Add files.
  const locked = !useGoal(canAddFiles)
  const status = locked && !rows.length ? 'Send your goal to unlock Add files.' : !rows.length ? 'Add files to continue' : progress ? `${progress.done} of ${progress.total} uploaded` : busy ? 'Checking the uploaded files' : !ready ? 'No file is ready yet' : 'Ready to start'
  const t = tips('1.1', 'Start')
  const { start, block, starting } = useIngestStart(snap)
  const canStart = status === 'Ready to start' && !block && !starting
  return <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text">{progress ? <UploadProgress {...progress} /> : <span className="muted sm">{status}</span>}</div>
    <Tip text={t.tip} note={status !== 'Ready to start' ? status : block ? fdeReason(block) : undefined}>
      <button className="btn btn-primary" aria-disabled={!canStart || undefined} onClick={() => { if (canStart) void start() }}>{starting ? 'Starting…' : 'Start ingestion'}</button></Tip>
  </div>
}
/** Parse is automated: status only; it opens Review when the extraction is done. A failure shows Retry. */
export function ParseFooter({ snap }: { snap: Snapshot }) {
  const rag = useRag(s => s)
  const ex = rag.steps.find(x => x.key === 'extract')
  const failed = rag.steps.slice(0, 2).find(x => x.status === 'failed')
  const reason = !snap.ingestion ? 'Starts from Upload once every file is ready' : failed ? `${failed.label} failed` : ex?.status !== 'done' ? 'Parsing the standardized files' : undefined
  useAdvanceWhen(!reason, () => goScreen('2.5')) // Review has no backend and is hidden: on to Context
  return <Foot title={reason ?? 'Extraction complete'} line="Extracted text and tables are linked to the source files">
    {failed && <GateButton icon="refresh" {...tips('1.9', 'Retry')} onClick={() => ragPipeline.retry(failed.key)}>Retry</GateButton>}</Foot>
}
/** Some file has been handed on from Standardize (Review leaves its waiting state). */
export const handedOn = (snap: Snapshot) => snap.uploads.some(u => snap.ingestion?.fileIds.includes(u.id))
export function IngestReviewFooter({ snap }: { snap: Snapshot }) {
  const rag = useRag(s => s)
  const parsed = !!snap.ingestion && rag.steps.find(x => x.key === 'extract')?.status === 'done'
  const closed = !snap.steps[0]!.editable
  return <Foot title={parsed ? 'Accepted, parsed files only' : 'Waiting for parsed files'} line="Context receives the accepted, parsed files">
    {/* Missed a file while reviewing? (5 Nov 2026) Upload opens its picker; current files and review results are kept. */}
    <button className="btn btn-secondary" disabled={closed} title={closed ? 'Uploads are closed.' : 'Add files you missed - current files and review results stay'}
      onClick={() => { setUi({ armPicker: 'files' }); goScreen('1.1') }}><Icon name="tb-plus" />Add files</button>
    <GateButton icon="tb-clipboard-check" {...tips('1.4', 'Approve')} reason={parsed ? undefined : 'Parsing must finish first'} onClick={() => goScreen('2.5')}>Approve</GateButton></Foot>
}

// ---- Quarantine -------------------------------------------------------------------------------------------
/** Files held out of ingestion, with the reason. There is no release or bypass: a held file never reaches RAG.
 *  Rescan exists for failed scans only (the backend rule); Remove keeps the object in quarantine for audit. */
export function Quarantine({ snap }: { snap: Snapshot }) {
  const { busy, run } = useAction()
  const editable = snap.steps[0]!.editable
  const held = snap.uploads.filter(u => u.scan === 'blocked' || u.scanError)
  const released = snap.uploads.filter(u => u.released)
  return (
    <main className="ing dc" aria-labelledby="center-title">
      <ScreenTitle title="Quarantine" help="Files held out of ingestion, with the reason." details={<SecurityDetails snap={snap} />} />
      {!held.length ? <p className="dc-none"><Icon name="shield" size={16} />No file is held.</p> : held.map(u => (
        <section key={u.id} className="dc-held">
          <div className="dc-held-main">
            <Icon name="zip" size={16} />
            <div><strong className="dc-name">{u.name}</strong><span className="muted xs">{u.scanError ? `Scan failed: ${u.scanError}` : scanView(u).text}</span></div>
            <span className={`dc-pill ${u.scanError ? 'tone-warn' : 'tone-err'}`}>{u.scanError ? 'Scan error' : 'Held'}</span>
          </div>
          {editable && <div className="dc-held-actions">
            {u.scanError && <button className="btn btn-outline" disabled={!!busy} onClick={() => run('Retry scan', () => api.retryScan(u.id), { step: 1 })}><Icon name="refresh" />Rescan</button>}
            <button className="dc-quiet-btn" disabled={!!busy} onClick={() => run('Remove file', () => api.removeUpload(u.id), { step: 1 })}>Remove</button>
          </div>}
        </section>
      ))}
      <div className="dc-rows">
        <details>
          <summary><strong>Released</strong><span className="dc-count">{released.length}</span><Icon name="down" size={13} /></summary>
          {released.length ? <FileRows files={released} /> : <p className="muted sm">No file released yet.</p>}
        </details>
      </div>
    </main>
  )
}

// ---- Connectors (optional) --------------------------------------------------------------------------------
const SOURCES = [
  { name: 'Google Drive', icon: 'cloud-up', desc: 'Folders and shared drives' },
  { name: 'SharePoint', icon: 'db', desc: 'Sites and libraries' },
  { name: 'S3', icon: 'folder', desc: 'Buckets and prefixes' },
]
export function Connectors({ snap }: { snap: Snapshot }) {
  return (
    <main className="ing dc" aria-labelledby="center-title">
      <ScreenTitle title="Connectors" help="Optional. Pull documents from a connected source." details={<SecurityDetails snap={snap} />} />
      <ul className="dc-list">
        {SOURCES.map(c => (
          <li key={c.name}>
            <Icon name={c.icon} size={16} />
            <div><strong>{c.name}</strong><span className="muted xs">{c.desc}</span></div>
            {/* No connector service in this build: the button stays, disabled, with its reason in the tooltip. */}
            <button className="btn btn-outline sm" aria-disabled title="Connector setup is not available yet" onClick={e => e.preventDefault()}>Connect</button>
          </li>
        ))}
      </ul>
    </main>
  )
}
