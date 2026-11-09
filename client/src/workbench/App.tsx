import { useEffect, useRef, useState } from 'react'
import { Refine } from '@refinedev/core'
import { api, dataProvider } from './api/adapter'
import type { Snapshot, StepId, StepInfo } from './api/types'
import { useAction, useSnapshot } from './lib/data'
import { getUi, setUi, useUi } from './lib/store'
import { activeScreen, aliasOf, goScreen, navigate, usePath } from './lib/route'
import { NavRail, TopBar } from './components/shell'
import { AssistantPane } from './components/AssistantPane'
import { AutoChip, Banner, Confirm, Empty, ErrorState, HitlChip, Icon, PanesContext, Skeleton } from './components/ui'
import * as Step1 from './steps/Step1'
import * as Step2 from './steps/Step2'
import * as Step3 from './steps/Step3'
import * as Step4 from './steps/Step4'
import { Connectors, FileDetail, IngestReviewFooter, handedOn, IngestionReview, ParseFooter, ParseView, Quarantine, UploadFooter } from './steps/Ingestion'
import { StandardizeFooter, StandardizeView } from './steps/Standardize'
import { UploadCount, UploadMenu, useUploadRows } from './steps/UploadPanes'
import { goalStage, useGoal } from './lib/goal.ts'
import { MastersPage } from './steps/Global'
import { ReleasePrep, SkillReview } from './steps/Review'
import { SCREEN_ROUTES, STEP_SCREEN, TASKS, globalByPath, screenByPath, screenLabel, stageOf, subtasksOf } from './tracker/trackerConfig.ts'
import { AutoContextFooter, QualityFooter, ReviewFooter, SyncFooter, SyncView, useContextAutomation } from './rag/RagSeamless'
import { RagScreen } from './rag/RagScreens'
import { ragPipeline, useRag } from './rag/ragPipeline.ts'
import { evidenceStatuses, previousRun } from './tracker/evidence.ts'
import { useApprovals } from './lib/approvals'
import { setTracker } from './tracker/useTrackerStore'
import { prerequisite } from './lib/prereq'
import { getDrafts } from './lib/drafts'
import { LoginPage } from './features/login/LoginPage'
import { useSession } from './features/login/session'

export interface StepProps { snap: Snapshot; info: StepInfo }

// Gated screens (a backend step locks them) rendered as 3 panes inside the Results & edits workspace, by screen ID.
// SOW, Skills grid and the three Validation views are centre-only (side panes removed per user, 14 Oct 2026).
const PANES: Record<string, { Left?: (p: StepProps) => React.ReactNode; Center: (p: StepProps) => React.ReactNode; Right?: (p: StepProps) => React.ReactNode }> = {
  '3.1': { Center: Step1.Center }, // SOW: centre-only too (Sources and SOW generation panes removed per user, 14 Oct 2026)
  '3.2': { Center: Step2.Center },
  '4.1': { Center: Step3.ActivitiesCenter },
  '4.3': { Center: Step3.CodeCenter },
  '4.2': { Center: Step3.TemplateCenter }, // Template: a real step (Ready · Running · Review · Failed · Approved)
  // Validation: centre-only. Sandbox · Check · Certify · Commit over the same backend step 4 and gates.
  '4.8': { Center: Step4.SandboxCenter },
  '4.4': { Center: Step4.TestCenter },
  '4.5': { Center: Step4.CertificationCenter },
  '4.6': { Center: Step4.HandoverCenter },
  '4.7': { Center: Step4.StagingCenter },
}
// Gated single-column screens (Upload and Scan share one intake component, state and run).
const SINGLE: Record<string, (p: StepProps) => React.ReactNode> = {
  '1.1': p => <Step1.Intake {...p} view="upload" />,
  '1.5': p => <Step1.Intake {...p} view="scan" />,
}
// One primary action per step, pinned below the scrolling body (never scrolled to). Gated screens show it only unlocked.
const FOOT: Record<string, (p: { snap: Snapshot; info?: StepInfo }) => React.ReactNode> = {
  '1.1': p => <UploadFoot snap={p.snap} />, '1.8': p => <StandardizeFooter snap={p.snap} />, '1.9': p => <ParseFooter snap={p.snap} />, '1.4': p => handedOn(p.snap) ? <IngestReviewFooter snap={p.snap} /> : null,
  // Index and Schema are automated: status only (Retry after a failure); they run and advance on their own.
  '2.5': () => <SyncFooter />, '2.1': () => <AutoContextFooter screen="2.1" />, '2.2': () => <AutoContextFooter screen="2.2" />, '2.3': p => <QualityFooter snap={p.snap} />, '2.4': () => <ReviewFooter />,
  '4.2': p => <Step3.TemplateFooter snap={p.snap} info={p.info!} />, '4.3': p => <Step3.TranslateFooter snap={p.snap} info={p.info!} />,
  '4.8': p => <Step4.SandboxFooter snap={p.snap} info={p.info!} />, '4.4': p => <Step4.CheckFooter snap={p.snap} info={p.info!} />,
  '4.5': p => <Step4.CertifyFooter snap={p.snap} info={p.info!} />, '4.6': p => <Step4.CommitFooter snap={p.snap} info={p.info!} />,
}
// Back button order (5 Nov 2026, per user): the visible screens as the rail lists them; Back opens the previous one.
const NAV_ORDER = TASKS.flatMap(t => subtasksOf(t.id))

// Ungated screens: always viewable, with honest empty/unavailable states.
const VIEWS: Record<string, (p: { snap: Snapshot; path: string }) => React.ReactNode> = {
  '1.3': FileDetail, '1.4': IngestionReview, '1.6': Connectors, '1.7': Quarantine, '1.8': StandardizeView, '1.9': ParseView,
  '2.5': SyncView, '2.1': RagScreen, '2.2': RagScreen, '2.3': RagScreen, '2.4': RagScreen,
  '3.3': SkillReview, '3.4': ReleasePrep,
}

export default function App() {
  const path = usePath()
  const session = useSession()
  // /login is public and has no shell; every other route needs a session (else /login?next=<where you were going>).
  const redirect = path === '/login' ? (session ? '/' : null) : session ? null : `/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`
  useEffect(() => { if (redirect) navigate(redirect, { replace: true }) }, [redirect])
  if (redirect) return null
  if (path === '/login') return <LoginPage />
  return (
    <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { refetchOnWindowFocus: false } } } } }}>
      <Workbench />
    </Refine>
  )
}

// Stage hand-off: RAG (2.x) starts only from 1.1's scan-gated "Start ingestion run", never on upload.
// A finished RAG run for an ingested batch generates the SOW (3.1).
function useStageHandoffs(snap?: Snapshot) {
  const synced = useRag(s => s.lastSyncAt)
  const prevSync = useRef(synced)
  useEffect(() => {
    if (synced === prevSync.current) return
    prevSync.current = synced
    if (!synced || !snap?.steps[0].editable || !snap.ingestion) return
    // No notices (removed 9 Oct 2026): 3.1 shows the generation state; a refusal is logged.
    api.startSowGeneration(ragPipeline.getState().runId).catch((e: Error) => console.error('SOW generation could not start:', e.message))
  }, [synced, snap])
}

// The tracker shows workflow evidence (recomputed from the live snapshot, so a reset backend or a new RAG run
// never leaves stale ticks); a manual status only shows while the evidence it overrode is unchanged.
function useTrackerEvidence(snap?: Snapshot) {
  const rag = useRag(s => s)
  const approvals = useApprovals()
  useEffect(() => {
    if (!snap) return
    const evidence = evidenceStatuses(snap, rag, approvals), previous = previousRun(snap, rag)
    const t = getUi().tracker
    if (JSON.stringify([evidence, previous]) !== JSON.stringify([t.evidence, t.previous])) setTracker({ evidence, previous })
  }, [snap, rag, approvals])
}

function Workbench() {
  const { snap, error, syncedAt, refetch } = useSnapshot()
  const theme = useUi(s => s.theme)
  const env = useUi(s => s.env)
  const drawer = useUi(s => s.drawer)
  const path = usePath()
  const screen = screenByPath(path)
  const global = globalByPath(path)
  const collapsed = useUi(s => s.collapsed)
  const shell = useUi(s => s.shell)
  const rag = useRag(s => s)
  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])
  // The FDE/Staging selector sets the palette: FDE = blue (default), Staging = orange. Deployed or not.
  useEffect(() => { document.documentElement.dataset.env = env }, [env])
  // Unsaved manifest/code drafts survive route changes (shared store); only leaving the app can lose them.
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { const d = getDrafts(); if (d.manifest !== undefined || Object.keys(d.code).length) e.preventDefault() }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [])
  useEffect(() => {
    if (!drawer) return
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setUi({ drawer: null }) }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [drawer])
  useStageHandoffs(snap)
  useContextAutomation(screen?.id)
  // Every screen opens at its top (per user, 5 Nov 2026): the workspace scroller is one persistent node, so without
  // this an Approve landed wherever the previous screen happened to be scrolled - often the end of the page.
  const wsBody = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = wsBody.current
    if (!el) return
    el.scrollTo({ top: 0 })
    // Pane bodies scroll their own content and their DOM nodes survive a screen change, so reset them too.
    el.querySelectorAll('.pane-body, .rag').forEach(x => { x.scrollTop = 0 })
  }, [screen?.id, global])
  useTrackerEvidence(snap)
  Step3.useRunHistory(snap)
  // Pane geometry never changes with the stage (21 Oct 2026, per user): only the person collapses or opens a pane.
  // Internal diagnostics (scan, quarantine, file detail) have no FDE role in this build: old URLs open Upload.
  useEffect(() => { if (screen?.item.internal) navigate(SCREEN_ROUTES['1.1'].path, { replace: true }) }, [screen])
  // Old paths (moved or renamed screens) redirect; "/" and unknown paths open wherever the work is right now.
  useEffect(() => {
    if (screen || global) return
    const alias = aliasOf(path)
    if (alias) navigate(alias + location.search, { replace: true })
    else if (snap) navigate(SCREEN_ROUTES[activeScreen(snap)]!.path, { replace: true })
  }, [snap, screen, global, path])

  if (!snap || (!screen && !global) || screen?.item.internal) return (
    <div className="app">
      <TopBar syncedAt={syncedAt} offline={!!error} />
      <main className="boot">
        {error
          ? <ErrorState title="Could not load the workflow" action={<button className="btn btn-primary" onClick={() => refetch()}><Icon name="refresh" />Retry</button>}>
              The backend did not respond ({error.message}). Nothing has been changed.
            </ErrorState>
          : <Skeleton rows={6} label="Loading workflow state" />}
      </main>
    </div>
  )

  const info = screen?.step ? snap.steps[screen.step - 1] : undefined
  const templateReady = screen?.id === '4.2' && info?.state === 'locked' && !snap.workflow && snap.sow?.status === 'approved'
  const locked = info?.state === 'locked' && !templateReady ? (prerequisite(snap, rag, info.id)?.reason ?? `${info.reason}.`) : undefined
  let body
  if (global) body = <MastersPage />
  else if (!info) {
    const View = VIEWS[screen!.id]
    body = <><ContextBar snap={snap} screenId={screen!.id} /><PanesContext.Provider value><View snap={snap} path={path} /></PanesContext.Provider></>
  }
  // Template before any run (the Grid is approved but the checks have not started): its Ready state, not a lock.
  else if (templateReady) body = <><ContextBar snap={snap} screenId={screen!.id} /><PanesContext.Provider value><main className="panes" data-single=""><Step3.TemplateReady /></main></PanesContext.Provider></>
  else if (info.state === 'locked') body = <LockedScreen id={screen!.id} info={info} snap={snap} />
  else if (SINGLE[screen!.id]) { const S = SINGLE[screen!.id]; body = <><ContextBar snap={snap} info={info} screenId={screen!.id} /><S snap={snap} info={info} /></> }
  else {
    const P = PANES[screen!.id]
    body = <>
      <ContextBar snap={snap} info={info} screenId={screen!.id} />
      <PanesContext.Provider value>
        <main className="panes" data-step={info.id} data-single={!P.Left && !P.Right ? '' : undefined} data-left={collapsed.left ? 'collapsed' : undefined} data-right={collapsed.right ? 'collapsed' : undefined}>
          {P.Left && <P.Left snap={snap} info={info} />}
          <P.Center snap={snap} info={info} />
          {P.Right && <P.Right snap={snap} info={info} />}
        </main>
      </PanesContext.Provider>
    </>
  }
  const title = global ? 'Masters' : screenLabel(screen!.id)
  const hint = global ? 'Admin' : screen!.item.description
  // One primary action, pinned below the body; a footer that renders nothing (Review while waiting) draws no bar.
  const foot = screen && FOOT[screen.id] && (info?.state !== 'locked' || templateReady) ? FOOT[screen.id]({ snap, info }) : null
  // Back goes to the previous screen in the rail order; hidden detail views go back to the screen they belong to.
  const prev = !screen || global ? undefined
    : screen.item.hidden && screen.item.navAs ? NAV_ORDER.find(s => s.id === screen.item.navAs)
    : NAV_ORDER[NAV_ORDER.findIndex(s => s.id === screen.id) - 1]
  return (
    <div className="app" data-env={env}>
      <a className="skip" href="#ws-title">Skip to screen content</a>
      <TopBar snap={snap} syncedAt={syncedAt} offline={!!error} />
      <UploadStage snap={snap} on={screen?.id === '1.1'} />
      <div className="shell" data-screen={screen?.id} data-nav={shell.nav ? 'collapsed' : undefined} data-assist={shell.assistant ? 'collapsed' : undefined} data-drawer={drawer ?? undefined}>
        <div className="shell-slot slot-nav"><NavRail snap={snap} /></div>
        {/* Mounted once for the session (never keyed by screen), so the conversation survives navigation. */}
        <div className="shell-slot slot-assist"><AssistantPane snap={snap} screenId={screen?.id} locked={locked} /></div>
        {drawer && <button className="drawer-scrim" aria-label="Close panel" onClick={() => setUi({ drawer: null })} />}
        <section className="shell-pane workspace" aria-labelledby="ws-title">
          <header className="shell-head ws-head">
            {screen?.id === '1.1'
              ? <UploadHead snap={snap} />
              : <>{prev && <button className="icon-btn sm ws-back" onClick={() => goScreen(prev.id, true)} title={`Back to ${prev.label}`} aria-label={`Back to ${prev.label}`}><Icon name="back" size={14} /></button>}<h2 id="ws-title" tabIndex={-1}><Icon name="table" size={13} /><span className="ws-kicker">Results &amp; edits</span><span className="ws-title">{title}</span></h2>{screen?.item.mode === 'automated' && <AutoChip className="ws-auto" />}{screen?.item.mode === 'hitl' && <HitlChip className="ws-auto" />}
                <span className="ws-hint" title={hint}>{hint}</span></>}
          </header>
          <div className="ws-body" ref={wsBody}>{body}</div>
          {/* One primary action, pinned below the scrolling body (never scrolled to). */}
          {foot && <footer className="ws-foot">{foot}</footer>}
        </section>
      </div>
    </div>
  )
}

// Upload's right pane (4 Nov 2026): Outputs. It is hidden until files are added (see useUploadStage).
function UploadHead({ snap }: { snap: Snapshot }) {
  return <><h2 id="ws-title" tabIndex={-1}><span className="ws-title up-title">Outputs</span><UploadCount snap={snap} /></h2><UploadMenu snap={snap} /></>
}
const UploadFoot = ({ snap }: { snap: Snapshot }) => <UploadFooter snap={snap} />
/** Pane progression on Upload: 1 = middle only, 2 = goal expanded (files pane opens), 3 = files added (outputs pane opens). */
function UploadStage({ snap, on }: { snap: Snapshot; on: boolean }) {
  const n = useUploadRows(snap).length, stage = useGoal(s => goalStage(s, n))
  useEffect(() => {
    const root = document.documentElement
    if (on) root.dataset.uploadStage = String(stage); else delete root.dataset.uploadStage
    return () => { delete root.dataset.uploadStage }
  }, [on, stage])
  return null
}

// The reason and the action come from the same prerequisite check, so they always agree.
function LockedScreen({ id, info, snap }: { id: string; info: StepInfo; snap: Snapshot }) {
  const rag = useRag(s => s)
  const b = prerequisite(snap, rag, info.id)
  return (
    <main className="locked-screen">
      <Empty icon="lock" title={`${screenLabel(id)} is locked`}
        action={b ? <button className="btn btn-primary" onClick={() => goScreen(b.target)}>{b.label}</button>
          : <button className="btn btn-primary" onClick={() => goScreen(activeScreen(snap))}>Go to {screenLabel(activeScreen(snap))}</button>}>
        {b?.reason ?? `${info.reason}.`}
      </Empty>
    </main>
  )
}

function ContextBar({ snap, info, screenId }: { snap: Snapshot; info?: StepInfo; screenId: string }) {
  const [confirm, setConfirm] = useState(false)
  const { busy, run } = useAction()
  // The Reopen button disappears once the screen is editable; keep keyboard focus on the screen instead of <body>.
  const reopening = useRef(false)
  useEffect(() => {
    if (reopening.current && info?.editable) { reopening.current = false; document.getElementById('center-title')?.focus() }
  }, [info?.editable])
  const name = screenLabel(screenId)
  const stepId = info?.id ?? 1
  const owner = STEP_SCREEN[stepId]
  const ownerName = screenLabel(owner)
  const canReopen = !!info && info.id < snap.activeStep && info.state !== 'locked'
  const later = ([2, 3, 4] as StepId[]).filter(n => n > stepId).map(n => screenLabel(STEP_SCREEN[n])).join(', ')
  const active = activeScreen(snap)
  const sandbox = ['4.1', '4.2', '4.3'].includes(screenId)
  // Only shown on a read-only screen (with Reopen). No background-work strip (notices removed 9 Oct 2026): it
  // appeared and vanished above the screen and pushed it up and down; each screen shows its own running state.
  const empty = !info || info.editable
  return (
    <div className={`contextbar ${empty ? 'is-empty' : ''}`}>
      {info && !info.editable &&
        <Banner tone={info.stale ? 'warn' : 'ok'} icon={info.stale ? 'refresh' : 'lock'}
            action={<span className="row">
              {canReopen && !sandbox && <button className="btn btn-secondary sm" onClick={() => setConfirm(true)} disabled={!!busy}><Icon name="refresh" />Reopen {ownerName}</button>}
              {active !== screenId && !sandbox && <button className="btn btn-ghost sm" onClick={() => goScreen(active)}>Go to {screenLabel(active)}</button>}
            </span>}>
            <strong>{name} is read-only.</strong> {info.reason}.
          </Banner>}
      <Confirm open={confirm} title={`Reopen ${screenLabel(owner)}?`} confirmLabel={`Reopen ${ownerName}`} onClose={() => setConfirm(false)}
        onConfirm={() => { setConfirm(false); reopening.current = true; void run('Reopen', () => api.reopen(stepId), { success: `${ownerName} reopened.${later ? ` ${later} marked stale.` : ''}`, step: stepId }).then(() => goScreen(owner)) }}
        consequences={[
          `${ownerName} becomes editable again${stepId === 1 ? ', and uploads reopen' : ''}.`,
          ...(later ? [`${later} will be marked stale and must be reviewed or re-run before the workflow can be deployed again.`] : []),
          ...(stepId === 1 ? ['The current SOW approval stays valid until you edit the document; any edit invalidates it.'] : []),
          'Whatever is deployed to Staging stays deployed until you deploy a new version.',
        ]} />
    </div>
  )
}
