// RAG stage, seamless layout (14 Oct 2026, BAM RAG seamless wireframes): Schemas, Context quality and RAG review
// bodies that fit the Results & edits pane, plus the four RAG footers (one primary action each, pinned). Data and
// handlers are the existing ones: ragPipeline (drafts, hand-off, context evaluation), the preflight questionnaire
// (`bam-preflight-checks`, read by SOW generation; contextQuestions.ts), the OKF bundle. Field edits and BullMind
// questions are typed or dictated in the Enterprise Assistant (AssistantPane).
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/adapter'
import type { Snapshot } from '../api/types'
import { layaJudgeContext, simulatedContextJudge } from '../api/sowEval'
import { browsedHere, goScreen } from '../lib/route'
import { setUi, useUi } from '../lib/store'
import { Confirm, Copy, Dialog, GateButton, Icon, Kpis, Time, Tip } from '../components/ui'
import { tips } from '../lib/actionTips'
import type { PreflightCheck } from './CogneePreflightCockpit'
import { GATE, isAsked, openFailure, removeAsked, retryQuestion, runSamples, sufficiency, syncBasis, useChecks, useOpenFailure, useRunState, type Row } from './contextQuestions'
import { masterLink } from '../steps/Global'
import { navigate } from '../lib/route'
import { JOBS, SCHEMAS } from './mockData'
import { ragPipeline, relations, stepStatus, useRag } from './ragPipeline'
import { OkfBundleCard } from './RagScreens'

// The Context quality questions (list + assistant thread) live in contextQuestions.ts.
export { GATE, sufficiency, useChecks } from './contextQuestions'

/** The IP context sufficiency evaluation RAG review needs (previously run by the context card on Context quality). */
export function useContextEvaluation() {
  const s = useRag(x => x)
  const ready = stepStatus(s, 'graph') === 'done'
  useEffect(() => {
    if (!ready || s.contextEvaluation || s.contextEvalStatus === 'running') return
    const req = { sources: JOBS.map(j => j.object), facts: relations(s).slice(0, 5).map(r => `${r.from} ${r.label} ${r.to}`), entities: SCHEMAS.map(x => x.name), schemas: SCHEMAS.map(x => x.name) }
    void layaJudgeContext(req).catch(() => simulatedContextJudge(req)).then(r => ragPipeline.setContextEvaluation(r))
  }, [ready, s.contextEvaluation, s.contextEvalStatus])
}

const Foot = ({ title, line, children }: { title: string; line?: React.ReactNode; children: React.ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>
    {children}
  </div>
)

// ---- footers -------------------------------------------------------------------------------------------------
// ---- Index and Schema are automated (16 Oct 2026, per user) --------------------------------------------------
// Sync's Start starts the automated run: Index advances to Schema once the graph is built; Schema sends
// every draft to the Workbench as soon as the proposals exist, then advances to Verify (the next human checkpoint).
// Opening Index or Schema from the rail only shows them (no bounce). A failed step pauses the flow on its screen
// (Retry there; the assistant names the failure) until it succeeds.
export function useContextAutomation(screenId?: string) {
  const s = useRag(x => x)
  const graph = stepStatus(s, 'graph'), schemas = stepStatus(s, 'schemas')
  const unsent = SCHEMAS.filter(x => !s.handoffs.some(h => h.schema === x.name && h.contextVersion === s.runId))
  // Schema step: the drafts go to the Workbench automatically (they stay drafts; nothing is approved).
  useEffect(() => { if (s.runId && s.contextStartedRun === s.runId && schemas === 'done' && unsent.length) for (const x of unsent) ragPipeline.sendToWorkbench(x.name) }, [s.runId, s.contextStartedRun, schemas, unsent.length])
  useEffect(() => {
    // Only once Sync's Start ran for this run, and never on a screen the person browsed to (no bounce).
    if ((screenId !== '2.1' && screenId !== '2.2') || !s.runId || s.contextStartedRun !== s.runId || browsedHere()) return
    const next = screenId === '2.1' && graph === 'done' ? '2.2' : screenId === '2.2' && schemas === 'done' && !unsent.length ? '2.3' : undefined
    if (!next) return
    const t = setTimeout(() => goScreen(next), 600) // just long enough to see the step finish
    return () => clearTimeout(t)
  }, [screenId, graph, schemas, unsent.length, s.runId, s.contextStartedRun])
}

/** Verify footer: Start (the sample questions), then Approve (opens Scope); Retry re-tests after a failure. The pass/fail rule is unchanged
 *  (contextQuestions.ts); no threshold is shown. Approve also needs the existing readiness checks, and
 *  starts SOW generation only when no SOW exists and none is running (never a duplicate job). */
export function QualityFooter({ snap }: { snap: Snapshot }) {
  const c = useChecks()
  const run = useRunState()
  const s = useRag(x => x)
  const rows = useReadiness()
  const graph = stepStatus(s, 'graph') === 'done'
  const passed = c.filter(x => x.status === 'passed').length, failed = c.filter(x => x.status === 'flagged').length
  const line = `${passed} of ${c.length} passed${failed ? ` · ${failed} need${failed === 1 ? 's' : ''} attention` : ''}`
  if (run.running) {
    const n = run.order.findIndex(id => c.find(x => x.id === id)?.status === 'running') + 1
    const t = tips('2.3', 'Start')
    return <Foot title={`Testing question ${Math.max(n, 1)} of ${run.order.length}`}>
      <Tip text={t.tip} note={t.tipBusy}><button className="btn btn-primary" aria-disabled><Icon name="spinner" spin />Testing…</button></Tip></Foot>
  }
  // Any failed answer blocks Approve until it passes (each one is retried from its Needs attention card).
  if (failed) {
    const first = c.find(x => x.status === 'flagged')!
    const why = `Approve is blocked - ${failed} failed answer${failed === 1 ? '' : 's'} must pass before moving on`
    return <Foot title={why} line={<>{line} · <button className="link xs" onClick={() => openFailure(first.id)}>Review issue</button></>}>
      <GateButton icon="tb-target" {...tips('2.3', 'Approve')} reason={why} onClick={() => {}}>Approve</GateButton></Foot>
  }
  const tested = run.phase === 'done' && !c.some(x => x.status === 'pending')
  if (tested && sufficiency(c) >= GATE) {
    const pending = rows.filter(r => !r.ok)
    const generating = snap.sowGeneration.some(x => x.status === 'running' || x.status === 'queued')
    const go = () => {
      ragPipeline.markVerified()
      if (!snap.sow && !generating) api.startSowGeneration(s.runId).catch((e: Error) => console.error('SOW generation could not start:', e.message))
      goScreen('3.1')
    }
    return <Foot title="Answers verified" line={line}>
      <GateButton icon="tb-target" {...tips('2.3', 'Approve')} reason={pending.length ? `Waiting for: ${pending.map(r => r.label).join(', ')}` : undefined} onClick={go}>Approve</GateButton></Foot>
  }
  return <Foot title={run.phase === 'idle' ? 'Test context with the sample questions' : line}>
    <GateButton icon="tb-shield-check" {...tips('2.3', 'Start')} reason={graph ? undefined : 'Available when the graph is built'} onClick={() => void runSamples()}>Start</GateButton>
  </Foot>
}

// ---- Sync ------------------------------------------------------------------------------------------------------
/** Admin Masters → Context Engine → extracted context. The master rows are EXAMPLE values (all shown connected, per
 *  user); nothing is ever marked synced. The context extraction rows show the real states of the current run. The
 *  masters are read-only here and private Filetype records are never shown. */
// EXAMPLE record counts for the Sync rows (per user, 15 Oct 2026); every master shown as connected.
const MASTERS: { name: string; icon: string; n: number; connected: boolean; version?: string }[] = [
  { name: 'Organization', icon: 'tb-table', n: 7, connected: true },
  { name: 'Rules', icon: 'tb-table', n: 8, connected: true },
  { name: 'Agents & Skills', icon: 'tb-table', n: 23, connected: true },
  { name: 'Tooling', icon: 'tb-table', n: 11, connected: true },
]
const STEP_PILL: Record<string, [string, string]> = { queued: ['Queued', ''], running: ['Extracting', 'tone-info'], done: ['Completed', 'tone-ok'], failed: ['Failed', 'tone-err'] }
export function SyncView() {
  const s = useRag(x => x)
  const steps = s.steps.filter(x => x.key === 'extract' || x.key === 'graph')
  return (
    <section className="dc rag-fit" aria-labelledby="center-title">
      <header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>Sync Admin Masters</h2><p className="muted sm">Sync Admin Masters for context extraction.</p></div></header>
      <ol className="sync-flow" aria-label="Direction"><li>Admin Masters</li><li aria-hidden><Icon name="chevron" size={12} /></li><li>Context Engine</li><li aria-hidden><Icon name="chevron" size={12} /></li><li>Extracted context</li></ol>
      <ul className="dc-list">
        {MASTERS.map(m => <li key={m.name}><Icon name={m.icon} size={15} /><div><strong>{m.name}</strong><span className="muted xs">{m.n} reference records · EXAMPLE masters{m.version ? ` · version ${m.version}` : ''}</span></div><span className={`dc-pill ${m.connected ? 'tone-ok' : ''}`}>{m.connected ? 'Connected' : 'Not connected'}</span></li>)}
        <li><Icon name="lock" size={15} /><div><strong>Filetype</strong><span className="muted xs">Private reference · FDE cannot edit</span></div><span className="dc-pill tone-info">Read-only</span></li>
      </ul>
      <p className="muted xs">Masters are not synced from here yet, so nothing is marked synced. The masters stay unchanged.</p>
      <section className="std-card" aria-labelledby="sync-ex">
        <h3 id="sync-ex">Context extraction</h3>
        {!s.runId ? <p className="muted sm">No run yet. It starts from Start in Upload.</p> : <ul className="dc-list">
          {steps.map(x => <li key={x.key}><Icon name={x.key === 'graph' ? 'graph' : 'tb-file-code'} size={15} /><div><strong>{x.label}</strong><span className={`xs ${x.status === 'failed' ? 'tone-err' : 'muted'}`}>{x.status === 'failed' ? x.error ?? 'No reason reported' : s.engine}</span></div>
            <span className={`dc-pill ${STEP_PILL[x.status]?.[1] ?? ''}`}>{x.status === 'running' && <Icon name="spinner" size={11} spin />}{STEP_PILL[x.status]?.[0] ?? x.status}</span></li>)}
        </ul>}
        {s.lastSyncAt && <p className="muted xs">Last context sync <Time iso={s.lastSyncAt} /> · run <Copy value={s.runId!} label="run ID" /></p>}
      </section>
    </section>
  )
}
export function SyncFooter() {
  const s = useRag(x => x)
  const graph = stepStatus(s, 'graph')
  // Index (graph build) and Schema run on their own after this checkpoint, so Sync needs the extraction only.
  const extract = stepStatus(s, 'extract')
  const exFailed = s.steps.slice(0, 2).find(x => x.status === 'failed')
  const reason = !s.runId ? 'No context run yet' : exFailed ? `${exFailed.label} failed: retry it in Parse` : extract !== 'done' ? 'The Context Engine is still extracting the context' : undefined
  return <Foot title="Admin Masters remain unchanged" line={reason ? undefined : graph === 'done' ? 'Context extracted · graph built' : 'Context extracted · Index and Schema run automatically next'}>
    <GateButton icon="tb-refresh" {...tips('2.5', 'Start')} reason={reason} onClick={() => { ragPipeline.markContextStarted(); goScreen('2.1') }}>Start</GateButton></Foot>
}
/** Index and Schema are automated: status only, and Retry in place of the action while a Context step has failed. */
export function AutoContextFooter({ screen }: { screen: '2.1' | '2.2' }) {
  const s = useRag(x => x)
  const failed = s.steps.find(x => x.status === 'failed')
  const st = stepStatus(s, screen === '2.1' ? 'graph' : 'schemas')
  return <Foot title={failed ? `${failed.label} failed` : !s.runId ? 'No context run yet' : st === 'done' ? (screen === '2.1' ? 'Graph built' : 'Drafts sent to the Workbench') : 'Runs automatically'}
    line={failed ? 'The flow waits here until it succeeds' : 'Moves on by itself to the next step'}>
    {failed && <GateButton icon="refresh" {...tips(screen, 'Retry')} onClick={() => ragPipeline.retry(failed.key)}>Retry</GateButton>}</Foot>
}

/** The four readiness checks RAG review shows and the footer gates on. */
export function useReadiness() {
  const s = useRag(x => x)
  const graph = stepStatus(s, 'graph') === 'done', schemas = stepStatus(s, 'schemas') === 'done'
  const sent = new Set(s.handoffs.filter(h => h.contextVersion === s.runId).map(h => h.schema)).size
  const ev = s.contextEvaluation
  const suff = ev?.metrics.find(m => m.key === 'sufficiency')?.score
  return [
    { icon: 'graph', label: 'Graph (Context Engine)', ok: graph, detail: graph ? `${relations(s).length} relations synced` : 'Graph build in progress' },
    { icon: 'schema', label: 'Schemas', ok: schemas && sent > 0, detail: !schemas ? 'Schema proposals in progress' : sent ? `${SCHEMAS.length} drafts, ${sent} sent to Workbench` : `${SCHEMAS.length} drafts, none sent to the Workbench yet` },
    { icon: 'gauge', label: 'Retrieval', ok: graph, detail: graph ? 'Answers cite source objects' : 'Available after the graph build' },
    { icon: 'file', label: 'IP context sufficiency', ok: ev?.verdict === 'sufficient', detail: ev ? `${suff ?? '—'}% confidence` : s.contextEvalStatus === 'running' ? 'Evaluating' : 'Evaluated after the graph build' },
  ]
}
export function ReviewFooter() {
  const s = useRag(x => x)
  const rows = useReadiness()
  const pending = rows.filter(r => !r.ok)
  return <Foot title={pending.length ? `${rows.length - pending.length} of ${rows.length} checks ready` : 'All checks ready'} line={pending.length ? `Waiting for: ${pending.map(r => r.label).join(', ')}` : 'The SOW is generated from this context'}>
    <GateButton action="generate" {...tips('2.4', 'Start')} reason={pending.length ? 'All checks must be ready' : undefined}
      onClick={() => { api.startSowGeneration(s.runId).catch((e: Error) => console.error('SOW generation could not start:', e.message)); goScreen('3.1') }}>Start</GateButton>
  </Foot>
}

// ---- Schemas -------------------------------------------------------------------------------------------------
export function SchemasView() {
  const s = useRag(x => x)
  const open = useUi(x => x.ragSchema ?? 'QuoteLine')
  const [info, setInfo] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  if (stepStatus(s, 'schemas') !== 'done') {
    const st = s.steps.find(x => x.key === 'schemas'), failed = s.steps.find(x => x.status === 'failed')
    return (
      <section className="dc rag-fit"><header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>Schema</h2><p className="muted sm">Drafts from context · runs automatically</p></div></header>
        {failed ? <div className="rr-warn tone-err" role="alert"><Icon name="alert" size={15} /><div><strong className="sm">{failed.label} failed</strong><span className="xs">{(failed.error ?? 'No reason reported').replace(/\.+$/, '')}. The flow waits here until it succeeds.</span></div>
            <button className="btn btn-secondary sm" onClick={() => ragPipeline.retry(failed.key)}><Icon name="refresh" />Retry</button></div>
          : <p className="dc-none">{st?.status === 'running' ? <Icon name="spinner" size={16} spin /> : <Icon name="clock" size={16} />}{!s.runId ? 'No context run yet.' : st?.status === 'running' ? 'Proposing schema drafts from the context graph…' : 'Waiting for the graph build. Schema drafts are proposed automatically.'}</p>}
      </section>
    )
  }
  const def = SCHEMAS.find(x => x.name === open) ?? SCHEMAS[0]
  const draft = s.drafts[def.name]
  const sent = new Set(s.handoffs.filter(h => h.contextVersion === s.runId).map(h => h.schema))
  return (
    <section className="dc rag-fit" aria-labelledby="center-title">
      <h2 id="center-title" className="sr-only" tabIndex={-1}>Schemas</h2>
      <div className="rs-chips" role="tablist" aria-label="Schemas">
        {SCHEMAS.map(x => <button key={x.name} role="tab" aria-selected={x.name === def.name} className="rs-chip" onClick={() => { setEditing(null); setUi({ ragSchema: x.name }) }}>
          {sent.has(x.name) && <Icon name="check" size={11} />}{x.name}</button>)}
      </div>
      <div className="rs-title">
        <strong>{def.name}</strong><span className="dc-pill muted">{sent.has(def.name) ? 'Sent' : 'Draft'}</span><span className="muted xs mono">{draft.version}</span>
        <span className="rs-actions">
          <button className="icon-btn sm" aria-label={editing === null ? 'Edit fields' : 'Save fields'} title={editing === null ? 'Edit fields' : 'Save fields'}
            onClick={() => { if (editing === null) setEditing(draft.code); else { ragPipeline.saveSchemaDraft(def.name, editing); setEditing(null) } }}><Icon name={editing === null ? 'edit' : 'save'} size={13} /></button>
          <button className="icon-btn sm" aria-label="Field notes" title="Field notes" onClick={() => setInfo(true)}><Icon name="info" size={14} /></button>
        </span>
      </div>
      {editing === null ? <pre className="rs-code"><code>{draft.code.trimEnd()}</code></pre>
        : <textarea className="rs-code rs-edit" value={editing} onChange={e => setEditing(e.target.value)} aria-label={`${def.name} fields`} spellCheck={false} />}
      <ul className="dc-list">
        <li><Icon name="table" size={15} /><div><span className="sm">Maps to {def.mapsTo} · {def.okfSource}</span></div><span className="dc-pill tone-ok">Ready</span></li>
        <li><Icon name="check" size={15} /><div><span className="sm">{sent.size} of {SCHEMAS.length} drafts sent to the Workbench</span></div><span className="dc-pill">{sent.size} of {SCHEMAS.length}</span></li>
      </ul>
      <Dialog open={info} title={`${def.name} field notes`} onClose={() => setInfo(false)} footer={<button className="btn btn-secondary" onClick={() => setInfo(false)}>Close</button>}>
        <dl className="kv">
          <div><dt>Fields</dt><dd className="mono xs">{def.fields}</dd></div>
          <div><dt>Source</dt><dd>{def.okfSource}</dd></div>
          <div><dt>Edited</dt><dd>{draft.editedAt ? <Time iso={draft.editedAt} /> : 'As proposed'}</dd></div>
          <div><dt>Webhook</dt><dd>On: field changes notify the Workbench draft</dd></div>
        </dl>
        <p className="muted sm">Types are inferred from parsed values. Schemas stay drafts; contracts are confirmed in the FDE Workbench.</p>
      </Dialog>
    </section>
  )
}

// ---- Context quality (Questions only) ---------------------------------------------------------------------------
const PILL: Record<PreflightCheck['status'], { t: string; c: string; i?: string }> = {
  pending: { t: 'Untested', c: '' }, running: { t: 'Testing', c: 'tone-info', i: 'spinner' }, passed: { t: 'Passed', c: 'tone-ok', i: 'check' }, flagged: { t: 'Failed', c: 'tone-err', i: 'x' } }
export function ContextQualityView() {
  useContextEvaluation()
  const c = useChecks()
  const s = useRag(x => x)
  // New ingestion (RAG run) or a schema edit resets every row to Untested.
  const basis = `${s.runId}|${Object.entries(s.drafts).map(([k, d]) => `${k}:${d.version}:${d.editedAt ?? ''}`).join(',')}`
  useEffect(() => { syncBasis(basis) }, [basis])
  const passed = c.filter(x => x.status === 'passed').length, failed = c.filter(x => x.status === 'flagged').length
  return (
    <section className="dc rag-fit cq" aria-labelledby="center-title">
      <header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>Verify</h2><p className="muted sm">Context testing · Questions only</p></div></header>
      <Kpis label="Context test summary" items={[
        { value: c.length, label: 'Questions' },
        { value: passed, label: 'Passed', tone: passed ? 'ok' : undefined },
        { value: failed, label: 'Needs attention', tone: failed ? 'err' : undefined },
      ]} />
      <NeedsAttention c={c} />
      <h3 className="cq-h">Context test results <span className="muted">Sample and asked</span></h3>
      <ul className="cq-list">
        {c.map(q => {
          const p = PILL[q.status], row = q as Row
          // A failed row scrolls to (and flashes) its card in Needs attention; passed rows keep their behaviour.
          return <li key={q.id} className={`cq-row is-${q.status}`} {...(q.status === 'flagged' ? { role: 'button', tabIndex: 0, 'aria-label': `Show failed answer: ${q.question}`,
            onClick: () => openFailure(q.id), onKeyDown: (e: React.KeyboardEvent) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openFailure(q.id) } } } : {})}>
            <span className="cq-kind">{isAsked(q) ? 'Asked' : 'Sample'}</span>
            <div className="cq-body">
              <span className="cq-q">{q.question}</span>
              {q.status === 'passed' && row.source && <span className="muted xs">{row.source}</span>}
              {q.status === 'flagged' && <span className="tone-err xs">{row.reason ?? 'No source in the graph'}</span>}
            </div>
            <span className={`dc-pill ${p.c}`}>{p.i && <Icon name={p.i} size={11} spin={q.status === 'running'} />}{p.t}</span>
            {isAsked(q) && q.status !== 'running' && <button className="icon-btn sm" aria-label={`Remove ${q.question}`} title="Remove" onClick={e => { e.stopPropagation(); removeAsked(q.id) }}><Icon name="x" size={11} /></button>}
          </li>
        })}
      </ul>
    </section>
  )
}

/** Needs attention (23 Oct 2026, per user): every failed answer pinned under the stat cards, above the results list,
 *  one card each (question, what went wrong, expected / found, source, Edit and Retry). First 2 shown, then Show all.
 *  `openFailure(id)` (a failed row, the footer's Review issue) scrolls to that card and flashes it. A card stays while
 *  its Retry runs; the section unmounts once nothing has failed. */
function NeedsAttention({ c }: { c: PreflightCheck[] }) {
  const items = (c as Row[]).filter(x => x.status === 'flagged' || (x.status === 'running' && x.failure))
  const [all, setAll] = useState(false), [flash, setFlash] = useState<string | null>(null)
  const ref = useRef<HTMLElement>(null)
  const openId = useOpenFailure()
  useEffect(() => {
    if (!openId) return
    openFailure(null)
    const i = items.findIndex(x => x.id === openId)
    if (i < 0) return
    if (i >= 2) setAll(true)
    setFlash(openId)
  }, [openId])
  useEffect(() => {
    if (!flash) return
    const card = ref.current?.querySelector<HTMLElement>(`[data-q="${CSS.escape(flash)}"]`)
    card?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    card?.focus({ preventScroll: true })
    const t = setTimeout(() => setFlash(null), 1600)
    return () => clearTimeout(t)
  }, [flash])
  if (!items.length) return null
  const shown = all ? items : items.slice(0, 2)
  return (
    <section ref={ref} className="cq-na" aria-labelledby="cq-na-h">
      <h3 id="cq-na-h" className="cq-na-h"><Icon name="alert" size={15} />Needs attention ({items.length})</h3>
      {shown.map(q => <FailedCard key={q.id} q={q} flash={flash === q.id} />)}
      {items.length > 2 && <button className="link sm cq-na-more" aria-expanded={all} onClick={() => setAll(v => !v)}>{all ? 'Show less' : `Show all ${items.length}`}</button>}
    </section>
  )
}

/** One failed answer. Everything comes from the row's failure data (contextQuestions.ts), never from this screen. */
function FailedCard({ q, flash }: { q: Row; flash: boolean }) {
  const f = q.failure ?? { reason: q.reason ?? 'No source in the graph', expected: 'An answer that names its source.', found: q.answer || 'No answer.', source: 'Context from the uploaded documents' }
  const to = masterLink(f.master)
  return (
    <article data-q={q.id} tabIndex={-1} className={`cq-na-card${flash ? ' is-flash' : ''}`} aria-label={`Failed answer: ${q.question}`}>
      <div className="cq-na-top"><strong className="cq-q">{q.question}</strong>
        <span className="dc-pill tone-err"><Icon name="x" size={11} />Failed</span></div>
      <p className="sm">{f.reason}</p>
      <dl className="cq-ef">
        <div><dt>Expected</dt><dd>{f.expected}</dd></div>
        <div><dt>Found</dt><dd>{f.found}</dd></div>
      </dl>
      <div><h4 className="cq-dh">Source</h4>
        <p className="sm">{f.source}{to && <> · <button className="link sm" onClick={() => navigate(to)}>Open in {f.master} master</button></>}</p></div>
      <div className="row cq-detail-actions">
        <GateButton icon="refresh" {...tips('2.3', 'Retry')} busy={q.status === 'running'} onClick={() => void retryQuestion(q.id)}>Retry</GateButton>
      </div>
    </article>
  )
}

// ---- RAG review ------------------------------------------------------------------------------------------------
export function RagReviewView({ snap }: { snap: Snapshot }) {
  useContextEvaluation()
  const s = useRag(x => x)
  const rows = useReadiness()
  const [dismissed, setDismissed] = useState(false)
  const unclassified = JOBS.filter((j, i) => j.okfClass === 'Unclassified' && s.jobs[i] === 'skipped')
  const failed = s.steps.filter(x => x.status === 'failed')
  return (
    <section className="dc rag-fit" aria-labelledby="center-title">
      <h2 id="center-title" className="rr-h">Readiness</h2>
      <ul className="dc-list rr-list">
        {rows.map(r => <li key={r.label}><Icon name={r.icon} size={15} /><div><strong className="sm">{r.label}</strong><span className="muted xs">{r.detail}</span></div>
          <span className={`dc-pill ${r.ok ? 'tone-ok' : 'muted'}`}>{r.ok ? 'Ready' : 'Pending'}</span></li>)}
      </ul>
      {failed.map(f => <p key={f.key} className="rr-warn tone-err"><Icon name="alert" size={15} />{f.label} failed: {f.error}</p>)}
      {unclassified.length > 0 && !dismissed && <div className="rr-warn" role="note">
        <Icon name="info" size={15} />
        <div><strong className="sm">{unclassified.length} file{unclassified.length === 1 ? '' : 's'} not in the graph</strong><span className="xs">{unclassified.map(j => j.object).join(', ')} · type not accepted</span></div>
        <button className="icon-btn sm" aria-label="Dismiss" title="Dismiss" onClick={() => setDismissed(true)}><Icon name="x" size={12} /></button>
      </div>}
      <div className="dc-rows">
        <details><summary><Icon name="file" size={15} /><strong>Knowledge bundle</strong><span className="muted sm">OKF v0.2</span><span className="rr-show">Show</span></summary>
          <div className="rr-body"><OkfBundleCard snap={snap} /></div></details>
        <details><summary><Icon name="info" size={15} /><strong>Provenance and run details</strong><span className="rr-show">Show</span></summary>
          <div className="rr-body">
            <dl className="kv">
              <div><dt>RAG run</dt><dd>{s.runId ? <Copy value={s.runId} label="run ID" /> : '—'}</dd></div>
              <div><dt>Engine</dt><dd>{s.engine}</dd></div>
              <div><dt>Synced</dt><dd>{s.lastSyncAt ? <Time iso={s.lastSyncAt} /> : '—'}</dd></div>
              <div><dt>Hand-offs</dt><dd>{s.handoffs.length ? s.handoffs.map(h => `${h.schema} ${h.version}`).join(', ') : 'None yet'}</dd></div>
            </dl>
            <p className="muted sm">{snap.uploads.length} uploaded files started this run; the graph uses the labelled EXAMPLE fixture dataset ({JOBS.length} objects).</p>
          </div></details>
      </div>
    </section>
  )
}
