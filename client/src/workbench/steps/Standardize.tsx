// Ingestion > Standardize: what the backend found out about each accepted file. Every file was scanned, type-checked and
// read (parsed) right after upload, so each already has its verdict; this screen shows it, read-only: ready, needs
// attention (read with notes) or cannot be read (with the reason). The original is never changed. Nothing is worked out in
// the browser. Start ingestion is the one action here: it starts the project's pipeline (ingestion, then the knowledge
// build, then the SOW) and opens Parse. Files that cannot be read stay out of Context.
import type { ReactNode } from 'react'
import type { Snapshot, UploadRecord } from '../api/types'
import { isReleased } from '../api/scanGate'
import { detectKind } from '../api/fileKind'
import { VERDICT_TEXT, fileVerdict, type Verdict, type VerdictState } from '../api/verdict'
import { goScreen } from '../lib/route'
import { tips } from '../lib/actionTips'
import { GateButton, Icon, Kpis, fmtBytes } from '../components/ui'
import { ScreenTitle, fdeReason, useIngestStart } from './IngestionFlow'

export interface StdFile { u: UploadRecord; v: Verdict }
/** Every accepted upload with the backend's verdict on it (used by Standardize, Review and Upload). */
export function useStandardize(snap: Snapshot): StdFile[] {
  return snap.uploads.filter(isReleased).map(u => ({ u, v: fileVerdict(u) }))
}
/** A file the FDE should look at before it goes on: read with notes, or not readable at all. */
export const needsReview = (f: StdFile) => f.v.state === 'attention' || f.v.state === 'failed'
export function useReviewFiles(snap: Snapshot) {
  const files = useStandardize(snap).filter(needsReview)
  return { files, count: files.length, ids: new Set(files.map(f => f.u.id)) }
}
/** The files that go on to Context: read successfully, with or without notes. */
export const usable = (list: StdFile[]) => list.filter(f => f.v.state === 'ready' || f.v.state === 'attention')

const TONE: Record<VerdictState, string> = { checking: 'tone-info', ready: 'tone-ok', attention: 'tone-warn', failed: 'tone-err' }
export const VerdictPill = ({ v }: { v: Verdict }) => <span className={`dc-pill ${TONE[v.state]}`}>{v.state === 'checking' && <Icon name="spinner" size={11} spin />}{VERDICT_TEXT[v.state]}</span>

// ---- screen ------------------------------------------------------------------------------------------------------
export function StandardizeView({ snap }: { snap: Snapshot }) {
  const list = useStandardize(snap)
  const n = (s: VerdictState) => list.filter(f => f.v.state === s).length
  const held = snap.uploads.length - list.length
  return (
    <main className="ing dc std" aria-labelledby="center-title">
      <ScreenTitle title="Standardize files" help="What the backend found when it read each accepted file. The originals are never changed." />
      <Kpis label="Standardize summary" items={[
        { value: n('ready'), label: 'Ready', tone: n('ready') ? 'ok' : undefined },
        { value: n('attention'), label: 'Needs attention', tone: n('attention') ? 'warn' : undefined },
        { value: n('failed'), label: 'Cannot be read', tone: n('failed') ? 'err' : undefined },
      ]} />
      {!list.length ? <p className="dc-none"><Icon name="tb-upload" size={16} />{snap.uploads.length ? 'Waiting for the uploads to be ready.' : 'No files yet. Add documents in Upload.'}</p> : (
        <ul className="dc-list std-files" aria-label="Files">
          {list.map(({ u, v }) => {
            const kind = detectKind(u.name)
            return <li key={u.id}>
              <Icon name="file" size={15} />
              <div>
                <strong className="dc-name">{u.name}</strong>
                <span className={`xs ${v.state === 'failed' ? 'tone-err' : 'muted'}`}>{[kind.type, fmtBytes(u.size), ...v.facts].join(' · ')}</span>
                <span className={`xs ${v.state === 'failed' ? 'tone-err' : 'muted'}`}>{v.reason}</span>
              </div>
              <VerdictPill v={v} />
            </li>
          })}
        </ul>
      )}
      {n('failed') > 0 && <p className="muted xs">A file that cannot be read stays out of Context. Remove it in Upload and add a readable copy.</p>}
      {n('attention') > 0 && <p className="muted xs">A file marked Needs attention was read, but some of it is missing or came with notes. It still goes to Context.</p>}
      {held > 0 && <p className="muted xs">{held} uploaded file{held === 1 ? ' is' : 's are'} not accepted yet or was refused; see the reason in Upload.</p>}
    </main>
  )
}

// ---- footer: one primary action, Start ingestion -----------------------------------------------------------------
export function StandardizeFooter({ snap }: { snap: Snapshot }) {
  const list = useStandardize(snap)
  const { start, block, starting } = useIngestStart(snap)
  const ok = usable(list)
  const checking = list.some(f => f.v.state === 'checking')
  const started = !!snap.ingestion
  const reason = started ? undefined : !list.length ? 'Add files in Upload first' : checking ? 'Checking files' : !ok.length ? 'No file could be read' : block ? fdeReason(block) : undefined
  const title = started ? 'Ingestion has started' : !list.length ? 'No ready files yet' : checking ? 'Checking files'
    : `${ok.length} file${ok.length === 1 ? '' : 's'} will go to Context${list.length > ok.length ? ` · ${list.length - ok.length} cannot be read` : ''}`
  const t = tips('1.1', 'Start')
  return <Foot title={title} line={started ? 'Parse shows the extraction that follows' : 'One start: ingestion, then the knowledge build, then the SOW'}>
    {started
      ? <button className="btn btn-primary" onClick={() => goScreen('1.9')}>Open Parse</button>
      : <GateButton icon="tb-player-play" {...t} reason={reason} busy={starting} onClick={() => { void start(ok.map(f => f.u.id)) }}>{starting ? 'Starting…' : 'Start ingestion'}</GateButton>}
  </Foot>
}

export const Foot = ({ title, line, children }: { title: string; line?: string; children: ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>
    {children}
  </div>
)
