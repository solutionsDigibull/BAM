// Workbench Template (4.2) and Translate (4.3), plus the hidden 4.1 activities detail: routes over ONE run of the check
// activities A–D. They share the editors, the backend document versions and one draft store (lib/drafts), so moving
// between them never loses or forks an unsaved edit. Human-gated (18 Oct 2026, per user): Template is a real step
// (Ready · Running · Review · Failed · Approved) that ends with the FDE's Approve; nothing opens the next step by itself.
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import Editor, { DiffEditor, type BeforeMount, type OnMount } from '@monaco-editor/react'
import { api } from '../api/adapter'
import type { Activity, CodeFile, Finding, Snapshot, StepInfo } from '../api/types'
import type { StepProps } from '../App'
import { useAction, useCode } from '../lib/data'
import { setUi, useUi } from '../lib/store'
import { goScreen, goStep } from '../lib/route'
import { setCodeDraft, setDrafts, unsaved, useDrafts } from '../lib/drafts'
import { certifiedBaseline, currentTests } from '../lib/certification'
import { Banner, Copy, Empty, ErrorState, GateButton, Icon, Kpis, Pane, Section, Skeleton, StatusPill, Time, fmtDuration, fmtTime, shortHash } from '../components/ui'
import { tips } from '../lib/actionTips'
import { approve, templateApproved, templateKey, useApprovals } from '../lib/approvals'

export const codeRev = (snap: Snapshot) => {
  const c = snap.workflow?.activities[2]
  return `${snap.workflow?.runId}:${c?.attempts}:${c?.status}:${c?.endedAt}:${snap.workflow?.manifest?.hash}`
}

export const defineThemes: BeforeMount = monaco => {
  monaco.editor.defineTheme('bam-dark', { base: 'vs-dark', inherit: true, rules: [], colors: { 'editor.background': '#0B1526', 'editor.lineHighlightBackground': '#101E32', 'editorGutter.background': '#0B1526' } })
  monaco.editor.defineTheme('bam-light', { base: 'vs', inherit: true, rules: [], colors: { 'editor.background': '#FFFFFF', 'editorGutter.background': '#FFFFFF' } })
  // Staging view: the editors follow the orange palette too.
  monaco.editor.defineTheme('bam-dark-staging', { base: 'vs-dark', inherit: true, rules: [], colors: { 'editor.background': '#1A1108', 'editor.lineHighlightBackground': '#24180C', 'editorGutter.background': '#1A1108' } })
  monaco.editor.defineTheme('bam-light-staging', { base: 'vs', inherit: true, rules: [], colors: { 'editor.background': '#FFFCF8', 'editorGutter.background': '#FFFCF8' } })
}
export const editorTheme = (theme: 'dark' | 'light', staging: boolean) => `bam-${theme}${staging ? '-staging' : ''}`

/** Open a file (and line) in the canonical 4.3 Generated code editor. */
export const openCode = (file: string, line?: number) => { setUi({ codeFocus: { file, line } }); goScreen('4.3') }

export function FindingItem({ f, files }: { f: Finding; files?: CodeFile[] }) {
  const file = f.file ?? files?.find(x => x.skillId === f.skillId)?.path
  return (
    <li className={`finding ${f.resolved ? 'resolved' : ''}`}>
      <StatusPill status={f.resolved ? 'succeeded' : f.severity === 'error' ? 'fail' : 'needs_review'} text={f.resolved ? 'Resolved' : f.severity === 'error' ? 'Error' : 'Warning'} />
      <span className="finding-msg">{f.message}</span>
      <span className="finding-refs">
        {f.skillId && <span className="mono xs">{f.skillId}</span>}
        {file && <button className="link xs" onClick={() => openCode(file, f.line)} title={`Open ${file}${f.line ? ` at line ${f.line}` : ''} in Translate`}>
          <Icon name="file" size={11} />{file}{f.line ? `:${f.line}` : ''}{!f.resolved && ' · open in Translate'}
        </button>}
      </span>
    </li>
  )
}

/** The FDE-facing names of the check activities (the backend labels stay as they are). */
export const CHECK_NAMES: Record<Activity['key'], string> = { A: 'ISATVON contract check', B: 'Dependency and runtime check', C: 'Translation check', D: 'Retry and fallback' }
/** D stands by until a check fails; until then it is "Not needed", never "skipped". */
const dIdle = (a: Activity) => a.key === 'D' && (a.status === 'queued' || a.status === 'skipped')

function ActivityCard({ a, editable, files, onRetry, busy }: { a: Activity; editable: boolean; files?: CodeFile[]; onRetry: () => void; busy: boolean }) {
  const dur = fmtDuration(a.startedAt, a.endedAt)
  const earlierFailures = a.history.filter(h => h.status === 'failed' && h.attempt < a.attempts)
  return (
    <li className={`activity st-${a.status}`}>
      <div className="activity-rail"><span className="activity-key" aria-hidden>{a.key}</span></div>
      <article className="activity-card" aria-label={`Activity ${a.key}: ${CHECK_NAMES[a.key]}, ${dIdle(a) ? 'not needed' : a.status}`}>
        <header>
          <h3><span className="sr-only">Activity {a.key}: </span>{CHECK_NAMES[a.key]}</h3>
          {dIdle(a) ? <StatusPill status="pending" text="Not needed" />
            : <StatusPill status={a.status} text={a.status === 'succeeded' && earlierFailures.length ? `Passed on attempt ${a.attempts}` : a.status === 'succeeded' ? 'Passed' : a.status === 'queued' ? 'Waiting' : undefined} />}
        </header>
        <dl className="meta">
          <div><dt>Attempts</dt><dd>{a.attempts}</dd></div>
          <div><dt>Duration</dt><dd>{dur ?? (a.status === 'running' ? 'in progress' : '—')}</dd></div>
          <div><dt>Started</dt><dd><Time iso={a.startedAt} /></dd></div>
        </dl>
        {a.detail && <p className="stage-detail">{a.detail}</p>}
        {a.status === 'failed' && a.reason && <p className="stage-error"><Icon name="alert" size={12} /> {a.reason}</p>}
        {a.history.length > 1 && (
          <details className="attempts">
            <summary>Attempt history ({a.history.length})</summary>
            <ol>{a.history.map(h => (
              <li key={h.attempt}>
                <span className="mono xs">#{h.attempt}</span>
                <StatusPill status={h.status} />
                <span className="muted xs">{fmtDuration(h.startedAt, h.endedAt) ?? ''} · <Time iso={h.endedAt} /></span>
                {h.reason && h.status === 'failed' && <span className="xs attempt-reason">{h.reason}</span>}
              </li>
            ))}</ol>
          </details>
        )}
        {a.findings.length > 0 && (
          <details className="findings" open={a.status === 'failed' || a.findings.some(f => !f.resolved)}>
            <summary>{a.findings.length} finding{a.findings.length === 1 ? '' : 's'}{a.findings.every(f => f.resolved) ? ' (all resolved)' : ''}</summary>
            <ul>{a.findings.map(f => <FindingItem key={f.id} f={f} files={files} />)}</ul>
          </details>
        )}
        {a.status === 'failed' && editable && (
          <button className="btn btn-secondary sm" onClick={onRetry} disabled={busy}><Icon name="refresh" />Retry from {a.key}</button>
        )}
      </article>
    </li>
  )
}

// ---- shared review state for 4.1–4.3 ------------------------------------------------------------
function useReview(snap: Snapshot, info: StepInfo) {
  const wf = snap.workflow
  const m = wf?.manifest
  const code = useCode(codeRev(snap))
  const drafts = useDrafts(d => d)
  const manifestJson = m ? JSON.stringify(m, null, 2) : ''
  const pending = unsaved(drafts, { code: Object.fromEntries((code.files ?? []).map(f => [f.path, f.content])), manifest: m ? manifestJson : undefined })
  const approved = !!m && wf?.approval?.hash === m.hash
  const running = wf?.status === 'running'
  const editable = info.editable && !running
  const lockedReason = approved ? `Approved ${fmtTime(wf!.approval!.at)} by ${wf!.approval!.by}. The manifest and code are locked.`
    : running ? 'The workflow is running; edit once it finishes.' : 'This screen is read-only.'
  const unsavedText = [pending.files.length ? `${pending.files.length} code file${pending.files.length === 1 ? '' : 's'} (Translate)` : '', pending.manifest ? 'manifest.json (Template)' : ''].filter(Boolean).join(' and ')
  const approveReason = !wf ? 'No validation run yet' : !info.editable ? (approved ? 'Already approved' : 'This screen is read-only')
    : running ? 'Wait for the workflow to finish' : wf.status !== 'succeeded' || !m ? 'Activities A–C must pass first (see open findings)'
    : pending.any ? `Save or discard your edits to ${unsavedText} first` : undefined
  const retryReason = !info.editable ? (approved ? 'Approved; the run is locked' : 'This screen is read-only') : running ? 'The workflow is already running' : undefined
  return { wf, m, ...code, drafts, pending, manifestJson, approved, running, editable, lockedReason, approveReason, retryReason }
}

/** End of Template, Translate and the validation activities: the run's status row with Retry. A render or AST
 *  failure stays a failure here and keeps Translate's Approve locked. The decisions sit in the pinned footers. */
function ReviewActions({ snap, info }: StepProps & { screen: '4.1' | '4.2' | '4.3' }) {
  const r = useReview(snap, info)
  const { busy, run } = useAction()
  const { wf } = r
  if (!wf) return null
  return (
    <div className="foot-stack end-actions">
      <div className="validation-row">
        <StatusPill status={r.running ? 'running' : wf.status === 'failed' ? 'fail' : 'pass'} text={r.running ? 'Translation running' : wf.status === 'failed' ? 'Translation failed' : 'Translation passed'} />
        <span className="muted sm">Check activities A–D (contract, dependencies, translation check, retry and fallback). Generated code is not certified: Sandbox and Check come before Certify and Commit.</span>
        <GateButton variant="secondary" icon="refresh" reason={r.retryReason} busy={busy === 'Retry workflow'}
          onClick={() => void run('Retry workflow', () => api.retryActivity('A'), { success: 'Workflow re-run started (A–D). Your code edits are kept.', step: 3 })}>Retry</GateButton>
      </div>
    </div>
  )
}

const FootIn = ({ title, line, children }: { title: string; line?: string; children: React.ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>{children}
  </div>
)
/** The workflow's Retry (A–D re-run; code edits are kept), shown in place of the primary while the run failed. */
function RetryRun({ snap, info, screen }: StepProps & { screen: '4.2' | '4.3' }) {
  const r = useReview(snap, info)
  const { busy, run } = useAction()
  return <GateButton icon="refresh" {...tips(screen, 'Retry')} reason={r.retryReason} busy={busy === 'Retry workflow'}
    onClick={() => void run('Retry workflow', () => api.retryActivity('A'), { step: 3 })}>Retry</GateButton>
}
/** Template footer, by state: Ready = Start (runs the check activities on the reviewed grid) · Running = status only ·
 *  Review = Edit + Approve (accepts the manifest and opens Translate) · Failed = Edit + Retry · Approved = status only.
 *  Nothing opens Translate when the run finishes: only Approve does. */
export function TemplateFooter({ snap, info }: StepProps) {
  const r = useReview(snap, info)
  const a = useApprovals()
  const { busy, run } = useAction()
  const draft = useDrafts(d => d.manifest)
  if (!r.wf) {
    const reason = snap.sow?.status !== 'approved' ? 'Approve the Scope first' : snap.activeStep !== 2 ? 'This screen is read-only' : undefined
    return <FootIn title="Ready" line="Nothing is saved to BAM_db yet">
      <GateButton icon="tb-player-play" {...tips('4.2', 'Start')} reason={reason} busy={busy === 'Start checks'}
        onClick={() => void run('Start checks', async () => { if (!snap.gridReviewedAt) await api.markGridReviewed(); await api.generateContract() }, { step: 3 })}>Start</GateButton></FootIn>
  }
  if (r.running) return <FootIn title="Running checks..." line="Contract, dependency and translation checks">{null}</FootIn>
  const done = templateApproved(a, r.wf)
  if (done) return <FootIn title="Approved" line={`manifest.json ${r.m?.version} · opens in Translate`}>{null}</FootIn>
  const failed = r.wf.status === 'failed'
  const editReason = !r.editable ? r.lockedReason : !r.m ? 'No manifest yet' : draft !== undefined ? 'Editing below' : undefined
  const dirty = draft !== undefined && draft !== r.manifestJson
  const approveReason = !info.editable ? 'This screen is read-only' : failed || !r.m ? 'Available once all checks pass.' : dirty ? 'Save or discard your manifest edits first' : undefined
  const n = r.wf.activities.filter(x => x.key !== 'D').length
  return <FootIn title={failed ? 'A check failed' : `${n} of ${n} checks passed`} line={failed ? 'Retry runs the checks again; your edits are kept' : `manifest.json ${r.m?.version} · review it, then approve`}>
    {failed ? <RetryRun snap={snap} info={info} screen="4.2" />
      : <GateButton action="approve" {...tips('4.2', 'Approve')} reason={approveReason}
          onClick={() => { if (draft !== undefined && !dirty) setDrafts({ manifest: undefined }); approve({ template: templateKey(r.wf) }); goScreen('4.3') }}>Approve</GateButton>}</FootIn>
}
/** Translate footer: Edit focuses the code editor; Approve approves the manifest and its translated code for this hash
 *  (approveManifest) and opens Validation on Sandbox (nothing runs until the FDE starts it there). Already approved:
 *  it only opens Sandbox. Template must be approved first; a translation failure (Retry replaces Approve) or unsaved
 *  edits keep it locked. */
export function TranslateFooter({ snap, info }: StepProps) {
  const r = useReview(snap, info)
  const a = useApprovals()
  const { busy, run } = useAction()
  const go = () => r.approved ? goStep(4) : void run('Approve artifact', () => api.approveManifest().then(() => true), { step: 3 }).then(ok => { if (ok) goStep(4) })
  const reason = r.approved ? undefined : !templateApproved(a, r.wf) && r.wf?.status === 'succeeded' ? 'Approve the manifest in Template first' : r.approveReason
  return <FootIn title={r.approved ? `Approved for ${r.m ? shortHash(r.m.hash) : 'this hash'}` : r.m ? 'Artifact ready for isolated validation' : 'No translated artifact yet'}
    line={r.approved ? `Approved ${fmtTime(r.wf!.approval!.at)}` : 'Approves this manifest and its code for the sandbox run'}>
    {r.wf?.status === 'failed' ? <RetryRun snap={snap} info={info} screen="4.3" />
      : <GateButton action="validate" {...tips('4.3', 'Approve')} reason={reason} busy={busy === 'Approve artifact'} successKey={r.approved ? r.wf?.approval?.at : undefined} onClick={go}>Approve</GateButton>}</FootIn>
}

// ---- Template run history (client-side: the backend keeps only the current run) ------------------------------------
// ponytail: last 10 finished runs in localStorage; read them from the backend once it keeps a run history.
export interface PastRun { key: string; runId: string; startedAt: string; endedAt?: string; status: string; activities: Activity[] }
const HKEY = 'bam-template-runs'
let runs: PastRun[] = (() => { try { return JSON.parse(localStorage.getItem(HKEY) ?? '[]') as PastRun[] } catch { return [] } })()
const runSubs = new Set<() => void>()
const useRuns = () => useSyncExternalStore(cb => { runSubs.add(cb); return () => { runSubs.delete(cb) } }, () => runs)
/** Records every finished run of the check activities (mounted once in App, so no finish is missed). */
export function useRunHistory(snap?: Snapshot) {
  const wf = snap?.workflow
  const key = wf && wf.status !== 'running' && wf.endedAt ? `${wf.runId}:${wf.endedAt}` : undefined
  useEffect(() => {
    if (!wf || !key || runs.some(x => x.key === key)) return
    runs = [{ key, runId: wf.runId, startedAt: wf.startedAt, endedAt: wf.endedAt, status: wf.status, activities: structuredClone(wf.activities) }, ...runs].slice(0, 10)
    try { localStorage.setItem(HKEY, JSON.stringify(runs)) } catch { /* storage blocked */ }
    runSubs.forEach(f => f())
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
}

function NoRun({ kicker, title }: { kicker: string; title: string }) {
  return (
    <Pane id="center-title" kicker={kicker} title={title} className="center">
      <Empty icon="clock" title="No manifest yet">Approve the Grid to run the check activities that write manifest.json and the translated code.</Empty>
    </Pane>
  )
}

function ChangesSwitch({ on, set, title }: { on: boolean; set: (v: boolean) => void; title: string }) {
  return (
    <label className="switch" title={title}>
      <input type="checkbox" role="switch" checked={on} onChange={e => set(e.target.checked)} />
      <span className="switch-track" aria-hidden><span /></span>Changes
    </label>
  )
}

function RunPill({ r }: { r: ReturnType<typeof useReview> }) {
  const wf = r.wf!
  return <StatusPill status={r.approved ? 'done' : r.running ? 'running' : wf.status === 'failed' ? 'failed' : 'needs_review'}
    text={r.approved ? 'Approved' : r.running ? 'Workflow running' : wf.status === 'failed' ? 'Workflow failed' : 'Awaiting approval'} />
}

// ---- 4.1 Activities ------------------------------------------------------------------------------
export function ActivitiesCenter({ snap, info }: StepProps) {
  const r = useReview(snap, info)
  const { busy, run } = useAction()
  const wf = r.wf
  if (!wf) return <NoRun kicker="Check · Validation activities" title="Validation activities" />
  const c = wf.activities[2], d = wf.activities[3]
  const cFirstFail = c.history.find(h => h.status === 'failed')
  const open = wf.activities.flatMap(a => a.findings.filter(f => !f.resolved))
  return (
    <Pane id="center-title" kicker="Check · Validation activities" title="Validation activities" className="center"
      actions={<RunPill r={r} />}>
      <dl className="kv inline">
        <div><dt>Workflow ID</dt><dd><Copy value={wf.workflowId} label="workflow ID" /></dd></div>
        <div><dt>Run ID</dt><dd><Copy value={wf.runId} display={`${wf.runId.slice(0, 13)}…`} label="run ID" /></dd></div>
        <div><dt>Started</dt><dd><Time iso={wf.startedAt} /></dd></div>
        <div><dt>Duration</dt><dd>{fmtDuration(wf.startedAt, wf.endedAt) ?? 'in progress'}</dd></div>
      </dl>
      {/* One narrative: a recovered failure is history, not a current failure. */}
      {d.status === 'succeeded' && c.status === 'succeeded' && cFirstFail && (
        <Banner tone="ok" icon="refresh">Recovered: C attempt {cFirstFail.attempt} failed ({cFirstFail.reason}); fallback D re-routed the payload; C passed on attempt {c.attempts}. The failed attempt stays in C's history. No open failure.</Banner>)}
      {d.status === 'succeeded' && c.status === 'failed' && <Banner tone="err" icon="alert">Fallback D ran, but C still fails on attempt {c.attempts}. Fix the open findings in Translate, then retry.</Banner>}
      {wf.status === 'failed' && !(d.status === 'succeeded' && c.status === 'failed') && <Banner tone="err" icon="alert">The workflow stopped. Failed activities stay listed below with their reason; fix the cause and retry.</Banner>}
      {open.length > 0 && (
        <Section title="Open findings" aside={<span className="muted sm">{open.length}</span>}>
          <ul className="findings compact">{open.map(f => <FindingItem key={f.id} f={f} files={r.files} />)}</ul>
        </Section>
      )}
      <ol className="timeline">
        {wf.activities.map(a => <ActivityCard key={a.key} a={a} files={r.files} editable={info.editable} busy={!!busy}
          onRetry={() => run(`Retry ${a.key}`, () => api.retryActivity(a.key), { step: 3 })} />)}
      </ol>
      <ReviewActions snap={snap} info={info} screen="4.1" />
    </Pane>
  )
}

// ---- 4.3 Generated code ----------------------------------------------------------------------------
export function CodeCenter({ snap, info }: StepProps) {
  const r = useReview(snap, info)
  const [showChanges, setShowChanges] = useState(true)
  if (!r.wf) return <NoRun kicker="Translation" title="Translate" />
  const findings = r.wf.activities.flatMap(a => a.findings)
  return (
    <Pane id="center-title" kicker="Translation" title="Translate" className="center"
      actions={<span className="row"><ChangesSwitch on={showChanges} set={setShowChanges} title="Colour-code edits against the generated code" /><RunPill r={r} /></span>}>
      {r.error ? <ErrorState title="Could not load generated code" action={<button className="btn btn-primary" onClick={() => r.refetch()}>Retry</button>}>{r.error.message}</ErrorState>
        : r.isLoading && !r.files ? <Skeleton rows={8} label="Loading generated code" />
        : !r.files?.length ? <Empty icon="file" title="No generated code yet">Activity C translates each skill and checks the result.</Empty>
        : <CodeView files={r.files} findings={findings} editable={r.editable} lockedReason={r.lockedReason} showChanges={showChanges} cStatus={r.wf.activities[2].status} />}
      <ReviewActions snap={snap} info={info} screen="4.3" />
    </Pane>
  )
}

// ---- 4.2 Template ------------------------------------------------------------------------------------
const PLAN: [Activity['key'], string][] = [['A', 'Validates every ISATVON field against the contract'], ['B', 'Checks the declared dependencies and runtime'],
  ['C', 'Translates each skill and checks the code'], ['D', 'Only if a check fails']]
/** Ready (no run yet, also shown while the step is still locked behind Grid): what will run. */
export function TemplateReady() {
  return (
    <Pane id="center-title" kicker="Check activities" title="Template" className="center" actions={<StatusPill status="pending" text="Ready" />}>
      <p className="muted sm">Runs on the reviewed grid when you start it here, or from Grid's Approve.</p>
      <ol className="timeline">{PLAN.map(([k, d]) => (
        <li key={k} className="activity st-queued"><div className="activity-rail"><span className="activity-key" aria-hidden>{k}</span></div>
          <article className="activity-card"><header><h3>{CHECK_NAMES[k]}</h3><StatusPill status="pending" text={k === 'D' ? 'Not needed' : 'Waiting'} /></header>
            <p className="stage-detail">{d}</p></article></li>))}</ol>
      <p className="muted sm"><Icon name="db" size={12} /> Nothing is saved to BAM_db yet.</p>
    </Pane>
  )
}
const ANNOUNCE: Record<string, string> = { A: 'Contract check', B: 'Dependency and runtime check', C: 'Translation check', D: 'Retry and fallback' }
export function TemplateCenter({ snap, info }: StepProps) {
  const r = useReview(snap, info)
  const a = useApprovals()
  const draft = useDrafts(d => d.manifest)
  const past = useRuns()
  const [open, setOpen] = useState<string | null>(null)
  const { busy, run } = useAction()
  const wf = r.wf
  if (!wf) return <TemplateReady />
  const approvedHere = templateApproved(a, wf)
  const checks = wf.activities.filter(x => x.key !== 'D')
  const passed = checks.filter(x => x.status === 'succeeded').length
  const c = wf.activities[2], d = wf.activities[3]
  const cFirstFail = c.history.find(h => h.status === 'failed')
  const failed = wf.status === 'failed'
  const last = [...wf.activities].filter(x => x.endedAt && !dIdle(x)).sort((x, y) => (x.endedAt! < y.endedAt! ? -1 : 1)).pop()
  const live = r.running ? (last ? `${ANNOUNCE[last.key]} ${last.status === 'succeeded' ? 'passed' : last.status}` : 'Running checks') : failed ? 'A check failed' : 'Checks finished, ready for review'
  const state = approvedHere ? ['done', 'Approved'] : r.running ? ['running', 'Running'] : failed ? ['failed', 'Failed'] : ['needs_review', 'Needs review']
  const shown = open ? past.find(x => x.key === open) : undefined
  const editing = r.editable && !approvedHere // the manifest edits in place (no Edit button)
  return (
    <Pane id="center-title" kicker="Check activities · manifest.json" title="Template" className="center"
      actions={<StatusPill status={state[0] as 'done'} text={state[1]} />}>
      <span className="sr-only" role="status" aria-live="polite">{live}</span>
      {r.running && <div className="tpl-progress" role="progressbar" aria-label="Checks finished" aria-valuemin={0} aria-valuemax={checks.length} aria-valuenow={passed}>
        <span style={{ width: `${(passed / checks.length) * 100}%` }} /></div>}
      {!r.running && !failed && <Kpis label="Check results" items={[
        { value: `${passed} of ${checks.length}`, label: 'Checks passed', tone: 'ok' },
        { value: fmtDuration(wf.startedAt, wf.endedAt) ?? '—', label: 'Duration' },
      ]} />}
      <dl className="kv inline">
        <div><dt>Run</dt><dd><Copy value={wf.runId} display={`${wf.runId.slice(0, 13)}…`} label="run ID" /></dd></div>
        <div><dt>Started</dt><dd><Time iso={wf.startedAt} /></dd></div>
        <div><dt>Duration</dt><dd>{fmtDuration(wf.startedAt, wf.endedAt) ?? 'in progress'}</dd></div>
      </dl>
      {d.status === 'succeeded' && c.status === 'succeeded' && cFirstFail && (
        <Banner tone="ok" icon="refresh">Recovered: C failed on attempt {cFirstFail.attempt} ({cFirstFail.reason}), fallback D re-routed it, C passed on attempt {c.attempts}.</Banner>)}
      {failed && <Banner tone="err" icon="alert">A check failed after retry and fallback. The failing check is open below with what went wrong; fix it (Edit, or the code in Translate) and Retry.</Banner>}
      <ol className="timeline">
        {wf.activities.map(x => <ActivityCard key={x.key} a={x} files={r.files} editable={info.editable} busy={!!busy}
          onRetry={() => run(`Retry ${x.key}`, () => api.retryActivity(x.key), { step: 3 })} />)}
      </ol>
      {!r.running && r.m && <section className="tpl-manifest" aria-labelledby="tpl-m">
        <h3 id="tpl-m" className="v3-card-title">Generated manifest <span className="muted xs mono">{r.m.version} · {shortHash(r.m.hash)}</span>
          {!editing && <Copy value={r.manifestJson} display="" label="manifest.json" />}</h3>
        {editing ? <ManifestView original={wf.generatedManifest ?? r.manifestJson} baseLabel="generated by the run" current={r.manifestJson} editable lockedReason={r.lockedReason} showChanges />
          : <pre className="rs-code tpl-json"><code>{r.manifestJson}</code></pre>}
      </section>}
      <details className="accordion tpl-history">
        <summary><Icon name="clock" size={12} />Run history<span className="muted xs">{past.length ? `${past.length} run${past.length === 1 ? '' : 's'}` : 'No finished runs yet'}</span></summary>
        <ul className="run-history">{past.map(h => (
          <li key={h.key}><button className="link sm" aria-expanded={open === h.key} onClick={() => setOpen(open === h.key ? null : h.key)}><Time iso={h.endedAt ?? h.startedAt} /></button>
            <StatusPill status={h.status === 'succeeded' ? 'pass' : 'fail'} text={h.status === 'succeeded' ? 'Passed' : 'Failed'} />
            <span className="muted xs">{fmtDuration(h.startedAt, h.endedAt) ?? '—'}</span></li>))}</ul>
        {shown && <ol className="timeline">{shown.activities.map(x => <ActivityCard key={x.key} a={x} editable={false} busy onRetry={() => {}} />)}</ol>}
      </details>
    </Pane>
  )
}

type MonacoEditor = Parameters<OnMount>[0]
type Monaco = Parameters<OnMount>[1]

/**
 * Editable Monaco view. With `showChanges` it is an inline diff against `original` (green added, red removed),
 * like the colour coding on the SOW and the grid; the right-hand (current) side is the one you edit.
 */
export function ReviewEditor({ path, language, original, value, editable, lockedReason, showChanges, onChange, onSave, onReady, startAtTop }: {
  path: string; language: string; original: string; value: string; editable: boolean; lockedReason: string; showChanges: boolean
  onChange: (v: string) => void; onSave: () => void; onReady?: (e: MonacoEditor, m: Monaco) => void
  /** Open at the beginning of the file (per user, 5 Nov 2026). Models are reused by path and Monaco restores their
   *  previous view state, so without this an Approve can land at the end of the file. */
  startAtTop?: boolean
}) {
  const theme = useUi(s => s.theme)
  const staging = useUi(s => s.env === 'Staging')
  const latest = useRef({ onChange, onSave, editable, value })
  latest.current = { onChange, onSave, editable, value }
  const bind = (e: MonacoEditor, m: Monaco) => {
    e.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => latest.current.onSave())
    // Only user typing is a draft: when the app syncs new content in (after a save or a re-run), the editor
    // already holds the value it was given, so that change is ignored.
    e.onDidChangeModelContent(() => {
      const v = e.getValue()
      if (latest.current.editable && v !== latest.current.value) latest.current.onChange(v)
    })
    onReady?.(e, m)
  }
  const options = {
    readOnly: !editable, readOnlyMessage: { value: lockedReason }, automaticLayout: true, minimap: { enabled: false }, glyphMargin: true,
    fontSize: 12.5, fontFamily: "'JetBrains Mono', monospace", scrollBeyondLastLine: false, tabSize: language === 'json' ? 2 : 4, insertSpaces: true,
  }
  const topOf = (e: MonacoEditor) => { e.setScrollTop(0); e.setPosition({ lineNumber: 1, column: 1 }) }
  return showChanges
    ? <DiffEditor key={`diff:${path}`} height="100%" language={language} original={original} modified={value}
        originalModelPath={`base/${path}`} modifiedModelPath={`current/${path}`} beforeMount={defineThemes} theme={editorTheme(theme, staging)}
        // Models are reused by path; letting the wrapper dispose them on unmount races the diff widget.
        keepCurrentOriginalModel keepCurrentModifiedModel
        onMount={(d, m) => {
          bind(d.getModifiedEditor(), m)
          if (!startAtTop) return
          topOf(d.getModifiedEditor())
          const sub = d.onDidUpdateDiff(() => { topOf(d.getModifiedEditor()); sub.dispose() })
        }} loading={<Skeleton rows={8} label="Loading editor" />}
        options={{ ...options, renderSideBySide: false, originalEditable: false, renderOverviewRuler: false, ignoreTrimWhitespace: false }} />
    : <Editor key={`edit:${path}`} height="100%" language={language} path={`current/${path}`} value={value} beforeMount={defineThemes}
        theme={editorTheme(theme, staging)} onMount={(e, m) => { bind(e, m); if (startAtTop) topOf(e) }} loading={<Skeleton rows={8} label="Loading editor" />}
        options={{ ...options, ariaLabel: `Editor for ${path}` }} />
}

function CodeView({ files, findings, editable, lockedReason, showChanges, cStatus }: {
  files: CodeFile[]; findings: Finding[]; editable: boolean; lockedReason: string; showChanges: boolean; cStatus: Activity['status']
}) {
  const focus = useUi(s => s.codeFocus)
  const drafts = useDrafts(d => d.code)
  const [path, setPath] = useState(files[0]?.path)
  const [saveError, setSaveError] = useState<string>()
  const [savedAt, setSavedAt] = useState<number>()
  const { busy, run } = useAction()
  const ed = useRef<MonacoEditor | null>(null)
  const mon = useRef<Monaco | null>(null)
  const deco = useRef<{ clear(): void } | null>(null)
  useEffect(() => { if (focus?.file && files.some(f => f.path === focus.file)) setPath(focus.file) }, [focus, files])
  const current = files.find(f => f.path === path) ?? files[0]
  const fileFindings = findings.filter(f => f.file === current?.path)
  const openHere = fileFindings.filter(f => !f.resolved)
  const draft = current ? drafts[current.path] : undefined
  // Unsaved = differs from the last saved content: the server's, or what was just saved while the reload is in flight.
  const [lastSaved, setLastSaved] = useState<Record<string, string>>({})
  const isDirty = (f: CodeFile) => drafts[f.path] !== undefined && drafts[f.path] !== f.content && drafts[f.path] !== lastSaved[f.path]
  const dirty = !!current && isDirty(current)
  const dirtyCount = files.filter(isDirty).length
  // A saved draft is dropped only once the server's content matches it, so the editor never flips back to old text.
  useEffect(() => {
    const done = files.filter(f => drafts[f.path] !== undefined && drafts[f.path] === f.content).map(f => f.path)
    if (done.length) setDrafts(d => { const code = { ...d.code }; for (const p of done) delete code[p]; return { code } })
  }, [files, drafts])

  const focusLine = focus?.file === current?.path ? focus?.line : undefined
  const reveal = () => {
    const line = focusLine
    deco.current?.clear()
    if (!ed.current || !mon.current || !line) return
    ed.current.revealLineInCenter(line)
    ed.current.setPosition({ lineNumber: line, column: 1 })
    deco.current = ed.current.createDecorationsCollection([{ range: new mon.current.Range(line, 1, line, 1), options: { isWholeLine: true, className: 'code-hl', glyphMarginClassName: 'code-glyph' } }])
  }
  useEffect(reveal, [focus, current?.path, showChanges])

  // Backend findings shown as squiggles on the lines they point at.
  useEffect(() => {
    const model = ed.current?.getModel()
    const m = mon.current
    if (!model || !m) return
    m.editor.setModelMarkers(model, 'bam', fileFindings.filter(f => !f.resolved && f.line && f.line <= model.getLineCount()).map(f => ({
      startLineNumber: f.line!, endLineNumber: f.line!, startColumn: 1, endColumn: model.getLineMaxColumn(f.line!),
      message: f.message, severity: m.MarkerSeverity.Error,
    })))
  })

  async function save() {
    if (!current || !dirty || !editable || busy) return
    setSaveError(undefined)
    const passed = await run('Save code', async () => {
      try { return await api.saveCode(current.path, draft!) } catch (e) { setSaveError((e as Error).message); throw e }
    }, { step: 3 })
    if (passed === undefined) return // save failed; the draft is kept
    setLastSaved(s => ({ ...s, [current.path]: draft! }))
    if (passed) setSavedAt(Date.now())
  }

  return (
    <div className="code-view">
      <div className="code-bar">
        <label className="field inline">File
          <select value={current?.path} onChange={e => setPath(e.target.value)}>
            {files.map(f => (
              <option key={f.path} value={f.path}>
                {f.path}{isDirty(f) ? ' • unsaved' : f.edited ? ' • edited' : ''}{findings.some(x => x.file === f.path && !x.resolved) ? ' • findings' : ''}
              </option>
            ))}
          </select>
        </label>
        {current && <Copy value={current.path} display="" label="file path" />}
        {current?.edited && !dirty && <span className="mark-edited"><Icon name="edit" size={10} />Edited by you</span>}
        {dirty && <span className="mark-unsaved" role="status"><Icon name="dot" size={10} />Unsaved changes</span>}
        {showChanges && <span className="diff-legend"><ins className="d-added">Added</ins><del className="d-removed">Removed</del><span className="muted xs">baseline: generated code</span></span>}
      </div>
      {!editable && <Banner tone="ok" icon="lock">{lockedReason}</Banner>}
      {saveError && <Banner tone="err" icon="alert"><strong>Save failed:</strong> {saveError}. Your edits are kept in the editor; fix the cause and save again.</Banner>}
      {dirty && editable && <Banner tone="warn" icon="refresh"><strong>Validation is stale for your edits.</strong> Activity C's translation check last ran on the saved version of this file. Save and re-validate to check the edited code.</Banner>}
      {!dirty && openHere.length > 0 && <Banner tone="err" icon="alert">Activity C's translation check fails on this file ({openHere.map(f => `line ${f.line}`).join(', ')}). Approval is blocked until it passes.</Banner>}
      {!dirty && cStatus === 'succeeded' && current?.edited && openHere.length === 0 && <Banner tone="ok" icon="check">Your edits to this file passed activity C's translation check.</Banner>}
      {fileFindings.length > 0 && <ul className="findings compact">{fileFindings.map(f => <FindingItem key={f.id} f={f} />)}</ul>}
      <div className="monaco-box">
        {current && <ReviewEditor path={current.path} language="python" original={current.generated} value={draft ?? current.content}
          editable={editable} lockedReason={lockedReason} showChanges={showChanges} startAtTop={!focusLine}
          onChange={v => setCodeDraft(current.path, v)} onSave={() => void save()}
          onReady={(e, m) => { ed.current = e; mon.current = m; reveal() }} />}
      </div>
      {editable && (
        <div className="code-actions">
          <span className="muted xs">{dirtyCount > 1 ? `${dirtyCount} files have unsaved changes · ` : ''}Saving re-runs activity C's translation check · Ctrl/Cmd+S</span>
          <button className="btn btn-ghost sm" disabled={!dirty || !!busy} onClick={() => current && setCodeDraft(current.path, undefined)}>Discard</button>
          <GateButton action="save" reason={dirty ? undefined : 'No changes to save'} busy={busy === 'Save code'} successKey={savedAt} onClick={save}>Save and re-validate</GateButton>
        </div>
      )}
    </div>
  )
}

function ManifestView({ original, baseLabel, current, editable, lockedReason, showChanges }: {
  original: string; baseLabel: string; current: string; editable: boolean; lockedReason: string; showChanges: boolean
}) {
  const draft = useDrafts(d => d.manifest)
  const setDraft = (v: string | undefined) => setDrafts({ manifest: v })
  const { busy, run } = useAction()
  const [saveError, setSaveError] = useState<string>()
  const [savedAt, setSavedAt] = useState<number>()
  const savedFrom = useRef<string | null>(null)
  // Once the saved manifest arrives (hash recomputed), the draft is replaced by it.
  useEffect(() => { if (savedFrom.current !== null && current !== savedFrom.current) { savedFrom.current = null; setDrafts({ manifest: undefined }) } }, [current])
  const dirty = draft !== undefined && draft !== current && savedFrom.current === null
  let invalid: string | undefined
  if (dirty) try { JSON.parse(draft) } catch (e) { invalid = (e as Error).message }
  async function save() {
    if (!dirty || !editable || busy || invalid) return
    const from = current
    setSaveError(undefined)
    await run('Save manifest', async () => {
      try { await api.saveManifest(draft!); savedFrom.current = from; setSavedAt(Date.now()) } catch (e) { setSaveError((e as Error).message); throw e }
    }, { success: 'Manifest saved. Version and hash updated; the approval for the previous hash no longer applies.', step: 3 })
  }
  return (
    <div className="code-view">
      <div className="code-bar">
        <span className="muted sm">Editable: <code>version</code>, agent and skill <code>name</code>s. <code>hash</code> and <code>createdAt</code> are recomputed on save.</span>
        {dirty && <span className="mark-unsaved" role="status"><Icon name="dot" size={10} />Unsaved changes</span>}
        {showChanges && <span className="diff-legend"><ins className="d-added">Added</ins><del className="d-removed">Removed</del><span className="muted xs">baseline: {baseLabel}</span></span>}
      </div>
      {!editable && <Banner tone="ok" icon="lock">{lockedReason}</Banner>}
      {invalid && <Banner tone="err" icon="alert"><strong>Invalid JSON:</strong> {invalid}. Fix it to save; your text is kept.</Banner>}
      {saveError && <Banner tone="err" icon="alert"><strong>Save failed:</strong> {saveError}. Your edits are kept.</Banner>}
      <div className="monaco-box">
        <ReviewEditor path="manifest.json" language="json" original={original} value={draft ?? current} editable={editable} lockedReason={lockedReason}
          showChanges={showChanges} startAtTop onChange={v => setDraft(v)} onSave={() => void save()} />
      </div>
      {editable && (
        <div className="code-actions">
          <span className="muted xs">Saving validates the JSON and clears the approval · Ctrl/Cmd+S</span>
          <button className="btn btn-ghost sm" disabled={!dirty || !!busy} onClick={() => setDraft(undefined)}>Discard</button>
          <GateButton action="save" reason={!dirty ? 'No changes to save' : invalid ? 'Fix the JSON first' : undefined} busy={busy === 'Save manifest'} successKey={savedAt} onClick={save}>Save manifest</GateButton>
        </div>
      )}
    </div>
  )
}

// Evidence only: no editing here. Links open the code in 4.3.
