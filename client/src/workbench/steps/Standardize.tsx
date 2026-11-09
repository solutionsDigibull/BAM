// Ingestion > Standardize (15 Oct 2026, BuLLM confirmed workflow + Standardize diagram). Classify → analysis copy if
// needed → identify problems → supporting docs → decision. The rules are in api/standardize.ts (pure, tested); this
// file reads each accepted upload's real bytes (the File objects held by the shared uploader) and keeps the FDE's
// manual-review decisions. The native original is never changed. Only accepted uploads appear here: files the
// upload checks hold back stay in Upload with their reason. Continue to Parse is the existing Start ingestion hand-off.
import { useEffect, useRef, useSyncExternalStore } from 'react'
import { useUppyState } from '@uppy/react'
import type { Snapshot, UploadRecord } from '../api/types'
import { isReleased } from '../api/scanGate.ts'
import { CONVERT_UNAVAILABLE, DIRECT_REF, FIX_UNAVAILABLE, ISSUES, NEEDS_PARSER, PDF_FIRST_REF, POST_PDF, PRE_PDF, STATE_TEXT, classify, decide, issueLabel, sniff,
  type Assessment, type IssueId } from '../api/standardize.ts'
import { ACCEPTED_TYPES } from '../api/types'
import { uppy } from '../lib/store'
import { goScreen } from '../lib/route'
import { useSession } from '../features/login/session'
import { Copy, GateButton, Icon, Time, fmtBytes } from '../components/ui'
import { tips } from '../lib/actionTips'
import { ScreenTitle, fdeReason, useIngestStart } from './IngestionFlow'

// ---- store: byte checks per upload, manual-review records, the open file -------------------------------------
type Read = { issues?: IssueId[]; unreadable?: boolean }
type Review = { at: string; by: string }
const RKEY = 'bam-standardize-reviews'
let reads: Record<string, Read> = {}
let reviews: Record<string, Review> = (() => { try { return JSON.parse(localStorage.getItem(RKEY) ?? '{}') } catch { return {} } })()
let open = null as string | null
let snapshot = { reads, reviews, open }
const subs = new Set<() => void>()
const emit = () => { snapshot = { reads, reviews, open }; subs.forEach(f => f()) }
const useStore = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => snapshot)
export const openFile = (id: string | null) => { open = id; emit() }

const SLICE = 64 * 1024
async function readBytes(u: UploadRecord) {
  if (reads[u.id]) return
  reads = { ...reads, [u.id]: {} }; emit()
  const f = uppy.getFiles().find(x => x.name === u.name && x.size === u.size)?.data as Blob | undefined
  let r: Read
  if (!f || typeof f.slice !== 'function') r = { unreadable: true }
  else {
    try {
      const [head, tail] = await Promise.all([f.slice(0, SLICE).arrayBuffer(), f.slice(Math.max(0, f.size - SLICE)).arrayBuffer()])
      r = { issues: sniff(u.name, u.size, new Uint8Array(head), new Uint8Array(tail)) }
    } catch { r = { unreadable: true } }
  }
  reads = { ...reads, [u.id]: r }; emit()
}
/** Re-run the checks (after a manual review): the decision is re-derived from the bytes, never from the click. */
function recheck(u: UploadRecord) { const { [u.id]: _, ...rest } = reads; reads = rest; emit(); void readBytes(u) }

export interface StdFile { u: UploadRecord; a: Assessment; review?: Review }
/** Every accepted upload with its Standardize assessment (used by Standardize, Parse, Review and the footer). */
export function useStandardize(snap: Snapshot): StdFile[] {
  const st = useStore()
  const files = snap.uploads.filter(isReleased)
  useEffect(() => { for (const u of files) if (!reads[u.id]) void readBytes(u) }, [files.map(u => u.id).join('|')])
  return files.map(u => {
    const r = st.reads[u.id], review = st.reviews[u.id]
    return { u, review, a: decide(classify(u.name), r && !r.unreadable ? r.issues : undefined, { reviewed: !!review, unreadable: r?.unreadable }) }
  })
}
/** The one "needs review" rule (round 4): a file Standardize holds for a person (Manual review or Local AI fix). Upload's
 *  row badge, the chat summary's count and Review's "Held for review" all read `useReviewFiles`, so they always agree. */
export const needsReview = (f: StdFile) => f.a.state === 'manual' || f.a.state === 'fix'
export function useReviewFiles(snap: Snapshot) {
  const files = useStandardize(snap).filter(needsReview)
  return { files, count: files.length, ids: new Set(files.map(f => f.u.id)) }
}
/** Why the standardized batch cannot continue yet (undefined when every accepted file is ready). */
export function standardizeBlock(list: StdFile[]): string | undefined {
  if (!list.length) return undefined // nothing accepted yet: the ingestion rule gives the reason
  if (list.some(f => f.a.state === 'checking')) return 'Checking files'
  const open = list.filter(f => f.a.state !== 'ready').length
  return open ? `Resolve the ${open} file${open === 1 ? '' : 's'} needing attention` : undefined
}

const TONE: Record<Assessment['state'], string> = { checking: 'tone-info', ready: 'tone-ok', manual: 'tone-warn', fix: 'tone-warn', failed: 'tone-err' }
const Pill = ({ a }: { a: Assessment }) => <span className={`dc-pill ${TONE[a.state]}`}>{a.state === 'checking' && <Icon name="spinner" size={11} spin />}{STATE_TEXT[a.state]}</span>
const DOCS = ['Troubleshooting guide', 'Acceptance checklist', 'Customer template']

// ---- screen ------------------------------------------------------------------------------------------------------
export function StandardizeView({ snap }: { snap: Snapshot }) {
  const list = useStandardize(snap)
  const st = useStore()
  const sel = list.find(f => f.u.id === st.open)
  if (sel) return <FileDetail f={sel} />
  const found = (id: IssueId) => list.filter(f => f.a.issues.includes(id)).length
  const n = (s: Assessment['state'][]) => list.filter(f => s.includes(f.a.state)).length
  const held = snap.uploads.length - list.length
  return (
    <main className="ing dc std" aria-labelledby="center-title">
      <ScreenTitle title="Standardize files" help="Classify → analysis copy if needed → identify problems → supporting docs → decision" />
      <section className="std-card" aria-labelledby="std-route">
        <h3 id="std-route">Route by file type</h3>
        <div className="std-two">
          <div><Icon name="copy" size={16} /><div><strong>Direct analysis</strong><span className="muted xs">{DIRECT_REF}</span></div></div>
          <div><Icon name="file-text" size={16} /><div><strong>PDF analysis copy first</strong><span className="muted xs">{PDF_FIRST_REF}</span></div></div>
        </div>
        <p className="std-note xs">The original is always kept. A PDF analysis copy is linked to its source and never replaces native checks: STEP keeps its geometry, dimensions, assemblies and fitment checks; DWG/DXF keep layers, scale, dimensions, blocks and CAD metadata.</p>
        <p className="muted xs">This build accepts {ACCEPTED_TYPES.map(t => t.slice(1).toUpperCase()).join(', ')}; a format in the reference is not supported until it is accepted.</p>
      </section>
      <section className="std-card" aria-labelledby="std-issues">
        <h3 id="std-issues">Identify file problems</h3>
        <ul className="std-chips">
          {ISSUES.map(i => {
            const c = found(i.id), na = NEEDS_PARSER.includes(i.id)
            return <li key={i.id} className={c ? 'on' : na ? 'na' : ''} title={na ? 'Not checked yet' : c ? `${c} file${c === 1 ? '' : 's'}` : 'Checked: not found'}>
              {i.label}{c > 0 && <span className="std-count">{c}</span>}{na && <span className="sr-only"> (not checked)</span>}</li>
          })}
        </ul>
        <p className="muted xs">Supporting docs: {DOCS.map((d, i) => <span key={d}>{i > 0 && ' · '}{d} <span className="std-na">unavailable</span></span>)}</p>
      </section>
      <section className="std-card" aria-labelledby="std-decide">
        <h3 id="std-decide">Decision &amp; remediation</h3>
        <div className="std-branches">
          <div><span className="dc-pill tone-ok">Yes</span><strong>Ready for Parse</strong><span className="muted xs">{n(['ready'])} file{n(['ready']) === 1 ? '' : 's'}</span></div>
          <div><span className="dc-pill tone-warn">No</span><strong>Local AI fix</strong><span className="muted xs">{n(['fix'])} file{n(['fix']) === 1 ? '' : 's'}</span></div>
          <div><span className="dc-pill tone-warn">Doubtful</span><strong>Manual review → Local AI fix</strong><span className="muted xs">{n(['manual'])} file{n(['manual']) === 1 ? '' : 's'}</span></div>
        </div>
      </section>
      {!list.length ? <p className="dc-none"><Icon name="tb-upload" size={16} />{snap.uploads.length ? 'Waiting for the uploads to be ready.' : 'No files yet. Add documents in Upload.'}</p> : (
        <ul className="dc-list std-files" aria-label="Files">
          {list.map(f => (
            <li key={f.u.id}>
              <Icon name={f.a.route === 'pdf-copy' ? 'tb-transform' : 'file'} size={15} />
              <button className="std-open" onClick={() => openFile(f.u.id)} aria-label={`${f.u.name}: ${STATE_TEXT[f.a.state]}. Open details`}>
                <strong className="dc-name">{f.u.name}</strong>
                <span className={`xs ${f.a.state === 'failed' ? 'tone-err' : 'muted'}`}>{f.a.route === 'pdf-copy' ? 'PDF analysis copy' : 'Direct analysis'} · {f.a.reason}</span>
              </button>
              <Pill a={f.a} />
            </li>
          ))}
        </ul>
      )}
      {held > 0 && <p className="muted xs">{held} uploaded file{held === 1 ? ' is' : 's are'} not ready yet; {held === 1 ? 'it appears' : 'they appear'} here once Upload shows Ready, or see the reason in Upload.</p>}
    </main>
  )
}

function FileDetail({ f }: { f: StdFile }) {
  const { u, a } = f
  // One file at a time: opening it starts at its top, with focus on its name.
  useEffect(() => { const h = document.getElementById('center-title'); h?.closest('.ws-body')?.scrollTo(0, 0); h?.focus() }, [u.id])
  const copy = a.route === 'pdf-copy'
  const parserChecks = NEEDS_PARSER.filter(i => !a.issues.includes(i))
  return (
    <main className="ing dc std" aria-labelledby="center-title">
      <button className="link sm std-back" onClick={() => openFile(null)}><Icon name="back" size={12} />Back to Standardize</button>
      <header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1} className="dc-name">{u.name}</h2><p className="muted sm">{a.reason}</p></div><Pill a={a} /></header>
      <section className="std-card">
        <h3>Original and analysis copy</h3>
        <dl className="kv">
          <div><dt>Original</dt><dd>{u.name} · {fmtBytes(u.size)} · source of record, never modified</dd></div>
          <div><dt>Object</dt><dd><Copy value={u.objectKey} label="object key" /></dd></div>
          <div><dt>Analysis copy</dt><dd>{copy ? `Not created. ${CONVERT_UNAVAILABLE}` : 'None needed: direct analysis of the original'}</dd></div>
          <div><dt>Conversion</dt><dd>{copy ? 'Not available. A person reviews the original instead.' : 'Not applicable'}</dd></div>
          <div><dt>Source relationship</dt><dd>{copy ? 'A copy would be linked to this original; the original stays the source for native checks' : 'Parse reads this original'}</dd></div>
        </dl>
      </section>
      <section className="std-card">
        <h3>Issues</h3>
        <ul className="dc-list">
          {a.issues.length ? a.issues.map(i => <li key={i}><Icon name="alert" size={15} /><div><strong>{issueLabel(i)}</strong><span className="muted xs">{u.name}</span></div>
            <span className="dc-pill tone-warn">{i === 'wrong-extension' ? 'Doubtful' : 'Found'}</span></li>)
            : a.state === 'checking' ? <li><Icon name="spinner" size={15} spin /><div><strong>Checking the file</strong></div></li>
            : <li><Icon name="check" size={15} /><div><strong>No problem found by the file checks</strong><span className="muted xs">Signature, extension, encryption and container checks on the uploaded bytes</span></div><span className="dc-pill tone-ok">Passed</span></li>}
          {parserChecks.length > 0 && <li><Icon name="info" size={15} /><div><strong>Not checked</strong><span className="muted xs">{parserChecks.map(issueLabel).join(', ')}: not checked yet</span></div><span className="dc-pill">Not run</span></li>}
        </ul>
      </section>
      <section className="std-card">
        <h3>Supporting docs</h3>
        <ul className="dc-list">{DOCS.map(d => <li key={d}><Icon name="file" size={15} /><div><strong>{d}</strong><span className="muted xs">No version published yet</span></div><span className="dc-pill">Unavailable</span></li>)}</ul>
      </section>
      <details className="std-card std-conv">
        <summary><strong>Conversion checks</strong><span className="muted xs">{copy ? 'Not run' : 'Not applicable: direct analysis'}</span><Icon name="down" size={13} /></summary>
        <div className="std-two">
          <div><div><strong>Pre-PDF</strong><ul className="muted xs">{PRE_PDF.map(x => <li key={x}>{x}</li>)}</ul></div></div>
          <div><div><strong>Post-PDF</strong><ul className="muted xs">{POST_PDF.map(x => <li key={x}>{x}</li>)}</ul></div></div>
        </div>
      </details>
      {f.review && <p className="muted xs">Manual review: original accepted by {f.review.by} · <Time iso={f.review.at} />. The file was not changed.</p>}
      {a.state === 'fix' && <p className="gate-reason"><Icon name="lock" size={12} />{FIX_UNAVAILABLE}</p>}
    </main>
  )
}

// ---- footer: one primary action (the open file's remediation, or Continue to Parse) ------------------------------
export function StandardizeFooter({ snap }: { snap: Snapshot }) {
  const list = useStandardize(snap)
  const st = useStore()
  const who = useSession()
  const { start, block, starting } = useIngestStart(snap)
  const sel = list.find(f => f.u.id === st.open)
  // Standardize is automated (17 Oct 2026, per user): once every file is checked, the Ready files are handed on by
  // themselves (also on arrival from Upload's Start) and the flow moves on to Parse and Review. Files needing manual
  // review or a fix are held out of Context, never passed silently: Review lists them with the reason. Accepting one
  // here later hands the larger set on again. A set already handed on is never started twice.
  const ready = list.filter(f => f.a.state === 'ready').map(f => f.u.id)
  const held = list.length - ready.length
  const checking = list.some(f => f.a.state === 'checking')
  const handedIds = snap.ingestion?.fileIds ?? []
  const handed = !!snap.ingestion && ready.length === handedIds.length && ready.every(id => handedIds.includes(id))
  const gate = block === 'These verified files are already ingested' ? undefined : block
  const lastStart = useRef('')
  const key = ready.join('|')
  const go = ready.length > 0 && !checking && !handed && !gate && !starting && lastStart.current !== key
  useEffect(() => { if (go) { lastStart.current = key; openFile(null); void start(ready) } }, [go, key]) // eslint-disable-line react-hooks/exhaustive-deps
  if (sel) {
    const a = sel.a
    const accept = () => {
      reviews = { ...reviews, [sel.u.id]: { at: new Date().toISOString(), by: who ?? 'FDE' } }
      try { localStorage.setItem(RKEY, JSON.stringify(reviews)) } catch { /* storage blocked */ }
      recheck(sel.u)
    }
    return <Foot title={`${sel.u.name}: ${STATE_TEXT[a.state]}`} line={a.state === 'manual' ? 'Accept the original as it is, or replace it in Upload' : a.state === 'ready' ? 'Back to Standardize to continue' : a.reason}>
      {a.state === 'manual' ? <GateButton icon="check" {...tips('1.8', 'Approve')} onClick={accept}>Approve</GateButton>
        : a.state === 'fix' ? <GateButton icon="sparkle" {...tips('1.8', 'Start')} tip="Runs the local AI fix on this file." reason="Local AI fix is not available yet" onClick={() => {}}>Start</GateButton>
        : null}
    </Foot>
  }
  // Status only (automated). Already handed on: Parse shows what follows.
  const heldText = held ? ` · ${held} held for review` : ''
  const title = !list.length ? 'No ready files yet' : checking ? 'Checking files' : handed ? `Handed ${ready.length} file${ready.length === 1 ? '' : 's'} to Parse${heldText}`
    : starting ? 'Handing the ready files to Parse' : !ready.length ? (standardizeBlock(list) ?? 'No ready files yet') : `${ready.length} file${ready.length === 1 ? '' : 's'} ready${heldText}`
  return <Foot title={title} line={gate && ready.length && !handed ? fdeReason(gate) : held ? 'Held files stay out of Context and are listed on Review' : 'Unresolved files never pass silently'}>{null}</Foot>
}

export const Foot = ({ title, line, children }: { title: string; line?: string; children: React.ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>
    {children}
  </div>
)
