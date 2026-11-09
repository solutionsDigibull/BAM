// Stage 2 sync screen and footers. The knowledge version, its coverage and its steps come from the backend
// through `ragPipeline`; nothing here is sample data and the masters are read, never edited.
import type { ReactNode } from 'react'
import { goScreen } from '../lib/route'
import { Copy, GateButton, Icon, Time } from '../components/ui'
import { tips } from '../lib/actionTips'
import { ragPipeline, stepStatus, useRag } from './ragPipeline'

const Foot = ({ title, line, children }: { title: string; line?: ReactNode; children?: ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>
    {children}
  </div>
)

const STEP_PILL: Record<string, [string, string]> = { queued: ['Queued', ''], running: ['Running', 'tone-info'], done: ['Completed', 'tone-ok'], failed: ['Failed', 'tone-err'] }
const DOC_PILL = (status: string): string => (status === 'FAILED' || status === 'ERROR' ? 'tone-err' : status === 'INCLUDED' || status === 'READY' ? 'tone-ok' : '')

export function SyncView() {
  const s = useRag(x => x)
  return (
    <section className="dc rag-fit" aria-labelledby="center-title">
      <header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>Knowledge build</h2><p className="muted sm">What the knowledge version covers: your documents and the Admin masters.</p></div></header>
      {s.masters && (
        <ul className="dc-list">
          <li><Icon name="tb-table" size={15} /><div><strong>Admin masters</strong><span className="muted xs">{s.masters.included} reference records · {s.masters.documents} master documents · read-only here</span></div><span className="dc-pill tone-info">Read-only</span></li>
        </ul>
      )}
      <section className="std-card" aria-labelledby="sync-docs">
        <h3 id="sync-docs">Documents</h3>
        {!s.runId ? <p className="muted sm">No knowledge version yet. It starts from Start in Upload.</p> : !s.docs.length ? <p className="muted sm">No documents are listed for this version.</p> : (
          <ul className="dc-list">
            {s.docs.map(d => (
              <li key={d.id}>
                <Icon name="tb-file-code" size={15} />
                <div><strong>{d.title}</strong><span className={`xs ${d.reason ? 'tone-err' : 'muted'}`}>{d.reason ?? `${d.chunks} chunks · ${d.entities} entities`}</span></div>
                <span className={`dc-pill ${DOC_PILL(d.status)}`}>{d.status.toLowerCase()}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="std-card" aria-labelledby="sync-ex">
        <h3 id="sync-ex">Build steps</h3>
        {!s.runId ? <p className="muted sm">No build yet.</p> : (
          <ul className="dc-list">
            {s.steps.map(x => (
              <li key={x.key}>
                <Icon name={x.key === 'graph' ? 'graph' : 'tb-file-code'} size={15} />
                <div><strong>{x.label}</strong><span className={`xs ${x.status === 'failed' ? 'tone-err' : 'muted'}`}>{x.status === 'failed' ? x.error ?? 'No reason reported' : x.detail ?? ''}</span></div>
                <span className={`dc-pill ${STEP_PILL[x.status]?.[1] ?? ''}`}>{x.status === 'running' && <Icon name="spinner" size={11} spin />}{STEP_PILL[x.status]?.[0] ?? x.status}</span>
              </li>
            ))}
          </ul>
        )}
        {s.lastSyncAt && <p className="muted xs">Last build finished <Time iso={s.lastSyncAt} />{s.runId && <> · version id <Copy value={s.runId} label="version id" /></>}</p>}
      </section>
    </section>
  )
}

export function SyncFooter() {
  const s = useRag(x => x)
  const graph = stepStatus(s, 'graph')
  const running = s.steps.some(x => x.status === 'running')
  const reason = !ragPipeline.canStart() ? 'Starting a build is not available' : running ? 'The knowledge build is still running' : undefined
  const label = s.runId ? 'Rebuild' : 'Start'
  return (
    <Foot title="Admin masters remain unchanged" line={!s.runId ? 'No knowledge version yet' : graph === 'done' ? 'Knowledge built · graph ready' : undefined}>
      {ragPipeline.canStart() && (
        <GateButton icon="tb-refresh" {...tips('2.5', 'Start')} reason={reason} onClick={() => { ragPipeline.start(); goScreen('2.1') }}>{label}</GateButton>
      )}
    </Foot>
  )
}

/** The graph screen is automated: status only, and Retry in place of the action while a step has failed. */
export function AutoContextFooter() {
  const s = useRag(x => x)
  const failed = s.steps.find(x => x.status === 'failed')
  const st = stepStatus(s, 'graph')
  return (
    <Foot title={failed ? `${failed.label} failed` : !s.runId ? 'No knowledge version yet' : st === 'done' ? 'Graph built' : 'Building'} line={failed ? failed.error : undefined}>
      {failed && ragPipeline.canStart() && <GateButton icon="refresh" {...tips('2.1', 'Retry')} onClick={() => ragPipeline.retry(failed.key)}>Retry</GateButton>}
    </Foot>
  )
}
