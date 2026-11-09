// Enterprise Assistant (middle pane), built on assistant-ui primitives with a local runtime. The thread lives for the
// whole session and is never keyed by screen, so navigating keeps the conversation. Declutter (14 Oct 2026): the pane
// is only the thread and the composer (input, Dictate, send). The chat holds no unprompted status notes
// (5 Nov 2026): an assistant message only answers something the person asked; the live screen note sits in the empty
// thread's greeting instead. On Ingestion screens the intake objective is captured through the composer and stored exactly as before
// (`fdeGoals`, used by SOW generation). Replies come from live workflow state (lib/assistant.ts); nothing here
// approves, certifies, hands over or deploys.
// Chat layout (22 Oct 2026, assistant pane design): ChatGPT-style thread. Empty = greeting + composer + starter chips;
// then accent user bubbles (editable; edit + resend regenerates), assistant replies beside a "B" avatar with an
// always-visible action bar, streamed with Stop. "+" attaches files to the chat for questions only: they are never
// uploaded or ingested (pack files go in through + Add files on the left).
import './assistant.css'
import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useUppyState } from '@uppy/react'
import { ActionBarPrimitive, AssistantRuntimeProvider, AttachmentPrimitive, AuiIf, ComposerPrimitive, MessagePartPrimitive, MessagePrimitive, ThreadPrimitive, useAuiState, useLocalRuntime, type AttachmentAdapter, type ChatModelAdapter, type ExportedMessageRepository, type ThreadMessage } from '@assistant-ui/react'
import { createDictation, fmtClock, stopDictation, useDictate } from './dictation'
import type { Snapshot } from '../api/types'
import { CONFIRM_OBJECTIVE, attachmentReply, chipReply, intentOf, objectiveReply, reply, screenNote, type AssistantFacts } from '../lib/assistant.ts'
import { prerequisite } from '../lib/prereq'
import { activeScreen } from '../lib/route'
import { setUi, uppy, useUi } from '../lib/store'
import { useRag } from '../rag/ragPipeline.ts'
import { STATUS_LABEL, TASKS, countedOf, doneCount, screenLabel, stageOf, taskStatus } from '../tracker/trackerConfig.ts'
import { useStatuses } from '../tracker/useTrackerStore'
import { intakeChecks, useSufficiency } from '../steps/IngestionFlow'
import { relations, ragPipeline } from '../rag/ragPipeline.ts'
import { JOBS, NODES, SCHEMAS } from '../rag/mockData.ts'
import { useReadiness } from '../rag/RagSeamless'
import { askQuestion, useChecks, useRunState, type Row } from '../rag/contextQuestions'
import { Icon, Tip, fmtBytes } from './ui'
import Orb from './reactbits/Orb'
import bull from '../assets/login/bull-head-transparent.png'
import { UploadMessages, fileCounts, useUploadRows } from '../steps/UploadPanes'
import { GoalCapture, GoalExpansion } from '../steps/GoalPanes'
import { applyEdit, atGoalEntry, continueGoal, conversationChips, draftReply, getGoal, setGoal, useGoal, type Draft } from '../lib/goal.ts'


const dictation = createDictation()
// Chat attachments stay in the thread (questions about a file); nothing is uploaded or ingested.
const chatFiles: AttachmentAdapter = {
  accept: '*',
  async add({ file }) { return { id: crypto.randomUUID(), type: 'file', name: file.name, contentType: file.type, file, status: { type: 'requires-action', reason: 'composer-send' } } },
  async send(a) { return { ...a, status: { type: 'complete' }, content: [] } },
  async remove() {},
}
const ScreenCtx = createContext<string | undefined>(undefined)
// The message awaiting Yes / No on "Save this as your intake objective?" (the buttons only show on that message).
const ConfirmCtx = createContext<string | null>(null)
// ---- one persistent thread per project (keep-conversation fix, 29 Oct 2026) ------------------------------------------
// The runtime lives in AssistantPane, which App mounts once for the session (never keyed by screen), so navigating never
// resets the thread. Stage and output updates are appended as ordinary assistant messages, once each (`posted`).
// TODO(backend): the snapshot has no project id yet, so every session is one project. Pass the real id here; each
// project's thread is swapped in and out of the runtime (`threads`). History lives in memory for the session only;
// persist `runtime.thread.export()` per project here when a conversation API exists.
const projectOf = (_snap: Snapshot) => 'default'
const THREAD_KEY = (project: string) => `bam-chat-thread:${project}`
/** Text-only copy of the thread (attachments hold File objects and are not saved). */
function saveThread(project: string, exp: ExportedMessageRepository) {
  try {
    const messages = exp.messages.map(m => ({ ...m, message: { ...m.message, attachments: [] } }))
    sessionStorage.setItem(THREAD_KEY(project), JSON.stringify({ ...exp, messages }))
  } catch { /* storage blocked or full: the in-memory copy still holds */ }
}
function loadThread(project: string): ExportedMessageRepository | null {
  try { const raw = sessionStorage.getItem(THREAD_KEY(project)); return raw ? JSON.parse(raw, (k, v) => (k === 'createdAt' && typeof v === 'string' ? new Date(v) : v)) as ExportedMessageRepository : null } catch { return null }
}
const threads = new Map<string, ExportedMessageRepository>()
/** Per project: stage events already in its thread (screen updates, Verify run results, upload summaries). */
/** Per project: upload record ids already acknowledged with "Files added." (see the effect in AssistantPane). */
const ackedIds = new Map<string, Set<string>>()
const ackedFor = (project: string) => { let s = ackedIds.get(project); if (!s) ackedIds.set(project, s = new Set()); return s }

export function AssistantPane({ snap, screenId, locked }: { snap: Snapshot; screenId?: string; locked?: string }) {
  const collapsed = useUi(s => s.shell.assistant)
  const objective = useUi(s => (s.fdeGoals ?? '').trim())
  const ragSchema = useUi(s => s.ragSchema ?? 'QuoteLine')
  const ask = useUi(s => s.assistantAsk)
  const pre = useChecks()
  const ready = useReadiness()
  const rag = useRag(s => s)
  const statuses = useStatuses()
  const inFlight = useUppyState(uppy, s => Object.values(s.files).filter(f => f.progress.uploadStarted && !f.progress.uploadComplete && !f.error).length)
  const suff = useSufficiency(snap.uploads.map(u => u.name))
  const intakeScreen = !!screenId && stageOf(screenId)?.id === '1'
  const checks = intakeChecks(snap, inFlight, suff)
  const uploadRows = useUploadRows(snap)
  const counts = fileCounts(uploadRows)
  const facts: AssistantFacts = {
    screen: screenId ? { id: screenId, label: screenLabel(screenId), stage: stageOf(screenId)?.label ?? '', locked } : undefined,
    snap,
    blocker: prerequisite(snap, rag, 4),
    stages: TASKS.map(t => ({ label: t.label, status: STATUS_LABEL[taskStatus(t.id, statuses)], done: doneCount(countedOf(t.id), statuses), total: countedOf(t.id).length })),
    active: screenLabel(activeScreen(snap)),
    statuses,
    intake: { objective, inFlight, checksReady: checks.filter(c => c.state === 'ready').length, checksTotal: checks.length, capture: intakeScreen && !objective,
      files: { total: counts.totalFiles, uploaded: counts.uploadedCount } },
    rag: { runId: rag.runId, steps: rag.steps.map(s => ({ label: s.label, status: s.status })), relations: relations(rag), nodes: rag.graphData?.nodes.length ?? NODES.length,
      schema: ragSchema, schemas: rag.steps.find(s => s.key === 'schemas')?.status === 'done' ? SCHEMAS.length : 0,
      checks: { passed: pre.filter(c => c.status === 'passed').length, total: pre.length }, ready: ready.map(r => ({ label: r.label, ok: r.ok })),
      unclassified: JOBS.filter((j, i) => j.okfClass === 'Unclassified' && rag.jobs[i] === 'skipped').length },
  }
  // The adapter is created once (stable runtime); it reads the latest facts through a ref.
  const latest = useRef(facts)
  latest.current = facts
  // An ambiguous intake message waits for Yes / No before it is saved as the objective.
  const [pending, setPending] = useState<string | null>(null)
  const pendingRef = useRef(pending)
  pendingRef.current = pending
  const adapter = useMemo<ChatModelAdapter>(() => {
    type Answer = { content: [{ type: 'text'; text: string }]; metadata?: { custom: Record<string, unknown> } }
    const answer = async (messages: readonly ThreadMessage[]): Promise<Answer> => {
      const last = messages[messages.length - 1]
      const text = (last?.content.map(p => (p.type === 'text' ? p.text : '')).join(' ') ?? '').trim()
      const f = latest.current
      // Chat attachments are for questions only (never ingested); no model reads them yet, so say so.
      const files = last?.role === 'user' ? last.attachments.map(a => a.name) : []
      if (files.length) return { content: [{ type: 'text', text: attachmentReply(files) }] }
      // The answer to "Save this as your intake objective?": Yes saves the earlier message, No answers it as a question.
      const asked = pendingRef.current
      if (asked && /^(yes|y|yes, save it|save it|no|n)\.?$/i.test(text)) {
        setPending(null)
        if (/^n/i.test(text)) return { content: [{ type: 'text', text: reply(asked, f) }] }
        setUi({ fdeGoals: asked })
        return { content: [{ type: 'text', text: objectiveReply(f) }] }
      }
      if (asked) setPending(null)
      // Upload's goal draft (29 Oct 2026): edits apply to the draft on the right (it updates at once); questions about
      // it are answered from it.
      const g = getGoal()
      if (f.screen?.id === '1.1' && g.draft) {
        const names = f.snap.uploads.map(u => u.name)
        const edit = applyEdit(g.draft, text)
        if (edit) { setGoal({ draft: edit.draft }); return { content: [{ type: 'text', text: edit.reply }] } }
        const r = draftReply(g.draft, text, names)
        if (r) return { content: [{ type: 'text', text: r }] }
      }
      // Suggestion chips are questions: answered, never saved as the objective.
      const canned = chipReply(text, f)
      if (canned) return { content: [{ type: 'text', text: canned }] }
      // The objective is captured through the composer and stored as before (fdeGoals, read by SOW generation). A
      // question is answered instead; an ambiguous message is confirmed first.
      const intent = f.intake?.capture ? intentOf(text) : 'question'
      if (intent === 'objective') {
        setUi({ fdeGoals: text })
        return { content: [{ type: 'text', text: objectiveReply(f) }] }
      }
      if (intent === 'ambiguous') {
        setPending(text)
        return { content: [{ type: 'text', text: CONFIRM_OBJECTIVE }], metadata: { custom: { confirm: text } } }
      }
      // Schemas: field edits are typed or dictated ("rename unit_price to price").
      const ren = /^rename\s+(\w+)\s+to\s+(\w+)\.?$/i.exec(text)
      if (ren && f.screen?.id === '2.2' && f.rag?.schema) {
        const name = f.rag.schema, d = ragPipeline.getState().drafts[name]
        const re = new RegExp(`\\b${ren[1]}\\b`)
        if (!d || !re.test(d.code)) return { content: [{ type: 'text', text: `${name} has no field named ${ren[1]}.` }] }
        ragPipeline.saveSchemaDraft(name, d.code.replace(new RegExp(`\\b${ren[1]}\\b`, 'g'), ren[2]))
        return { content: [{ type: 'text', text: 'Done. The Workbench draft updates automatically.' }] }
      }
      // Context quality: every question (typed or dictated) is tested against the graph and added to the same list.
      const bull = /^ask bullmind:?\s*(.+)$/i.exec(text)
      if (f.screen?.id === '2.3' && !/^(what('s| is) next|why|status|progress)\b/i.test(text)) {
        const r = await askQuestion(bull ? bull[1] : text)
        return { content: [{ type: 'text', text: r.answer }], metadata: { custom: { source: r.status === 'passed' ? (r as Row).source : undefined, failed: r.status !== 'passed' } } }
      }
      return { content: [{ type: 'text', text: reply(text, f) }] }
    }
    return {
      // The reply streams in (blinking cursor); Stop aborts it and keeps what has arrived.
      async *run({ messages, abortSignal }) {
        const a = await answer(messages)
        const full = a.content[0].text
        for (let i = 4; ; i += 4) {
          if (abortSignal.aborted) return
          yield { ...a, content: [{ type: 'text', text: full.slice(0, i) }] }
          if (i >= full.length) return
          await new Promise(r => setTimeout(r, 16))
        }
      },
    }
  }, [])
  const runtime = useLocalRuntime(adapter, { adapters: { attachments: chatFiles, ...(dictation && { dictation }) } })
  // A screen asks something (a graph node click): post it once, so the assistant answers in the thread.
  const asked = useRef(0)
  useEffect(() => {
    if (!ask || ask.id === asked.current) return
    asked.current = ask.id
    runtime.thread.append({ role: 'user', content: [{ type: 'text', text: ask.text }] })
  }, [ask, runtime])
  const project = projectOf(snap)
  // Keep-conversation fix (5 Nov 2026): the pane unmounts whenever App shows its loading / error / redirect screen
  // (snapshot refetch, route redirect), which threw the runtime and the whole chat away. The thread is therefore
  // restored from the module store on mount and saved there after every change (not while a reply streams), and also
  // kept in sessionStorage so a reload keeps it too.
  useEffect(() => {
    const saved = threads.get(project) ?? loadThread(project)
    if (saved?.messages.length && runtime.thread.getState().messages.length === 0) { try { runtime.thread.import(saved) } catch { /* unreadable saved thread: start empty */ } }
    return runtime.thread.subscribe(() => {
      if (runtime.thread.getState().isRunning) return
      const exp = runtime.thread.export()
      if (!exp.messages.length) return // an empty thread never replaces a saved one (a cleared chat is not an unmount)
      threads.set(project, exp)
      saveThread(project, exp)
    })
  }, [runtime]) // eslint-disable-line react-hooks/exhaustive-deps
  // Switching projects: park this project's thread and load the other one's (never mixed).
  const shownProject = useRef(project)
  useEffect(() => {
    if (shownProject.current === project) return
    threads.set(shownProject.current, runtime.thread.export())
    runtime.thread.import(threads.get(project) ?? { messages: [] })
    shownProject.current = project
  }, [project, runtime])
  // Upload progress and "files added" are not posted in chat (4 Nov 2026): the files and outputs panes hold every count and reason.
  // The live screen note (5 Nov 2026): shown in the empty thread's greeting, never posted as a chat message.
  const note = screenNote(facts)
  // Verify (5 Nov 2026): a finished run is not posted in chat - chat only answers questions a person asks.
  // The sample results, failed answers and the gate line stay in the Verify panes.
  const toggle = () => setUi(s => ({ shell: { ...s.shell, assistant: !s.shell.assistant } }))
  // Upload: chips generated from the draft while it is the output on screen.
  const goal = useGoal(s => s)
  // Goal flow (29 Oct 2026): the chips wait until files are uploaded, then come from the goal's draft (ThreadBody).
  const chipDraft = screenId === '1.1' && counts.uploadedCount > 0 ? goal.draft : null

  // Hidden by the person only (Hide assistant); a stage change never hides it. The runtime provider stays mounted, so
  // the thread keeps its messages and background posts (the upload summary) still reach it while the pane is hidden.
  if (collapsed) return (
    <AssistantRuntimeProvider runtime={runtime}>
      <aside className="shell-pane shell-rail" aria-label="BuLLM Assistant">
        <button className="rail-show" onClick={toggle} aria-expanded={false} aria-label="Show assistant">
          <Icon name="panel-right" size={16} /><span>Show assistant</span>
        </button>
      </aside>
    </AssistantRuntimeProvider>
  )
  return (
    <AssistantRuntimeProvider runtime={runtime}>
    <ConfirmCtx.Provider value={pending}>
      <aside className="shell-pane assist" aria-label="BuLLM Assistant" data-screen={screenId}>
        {/* Header row removed per user (5 Nov 2026): no "BuLLM Assistant" title or agent name; the Hide assistant
            control stays, right-aligned, and only where it already appeared (all three panes in). */}
        <header className="shell-head">
          <button className="btn btn-ghost sm as-hide" onClick={toggle} aria-expanded aria-label="Hide assistant"><Icon name="panel-left" size={14} />Hide assistant</button>
        </header>
        <ScreenCtx.Provider value={screenId}>
          <ThreadBody snap={snap} screenId={screenId} note={note} capture={!!facts.intake?.capture} files={counts.totalFiles}
            chipDraft={chipDraft} />
        </ScreenCtx.Provider>
      </aside>
    </ConfirmCtx.Provider>
    </AssistantRuntimeProvider>
  )
}

/** Empty: the greeting with the composer and starter chips centred. Otherwise the thread, then the composer. */
function ThreadBody({ snap, screenId, note, capture, files, chipDraft }: { snap: Snapshot; screenId?: string; note: string; capture: boolean; files: number; chipDraft: Draft | null }) {
  const empty = useAuiState(s => s.thread.messages.length === 0) && screenId !== '2.3'
  // Chips (goal-flow fine-tuning v2, 29 Oct 2026): only from the draft, only once files are uploaded, at most two.
  // Messages sent before the chips appeared do not count; after the first one sent since, only follow-ups to the
  // latest message show (conversationChips), and none while a reply is still streaming.
  const userTexts = useAuiState(s => s.thread.messages.filter(m => m.role === 'user').map(m => m.content.map(p => (p.type === 'text' ? p.text : '')).join(' ').trim()).join('\u0000'))
  const running = useAuiState(s => s.thread.isRunning)
  const sentAll = userTexts ? userTexts.split('\u0000') : []
  const baseline = useRef<number | null>(null)
  if (!chipDraft) baseline.current = null
  else if (baseline.current === null) baseline.current = sentAll.length
  const since = baseline.current === null ? 0 : sentAll.length - baseline.current
  const names = snap.uploads.map(u => u.name).join('\u0000')
  const hasDraft = useGoal(s => !!s.draft)
  // Fresh entry (5 Nov 2026): a revived saved goal shows the goal capture first, not the expanded draft, whatever
  // the project's progress; Enter (or the first question asked) continues the saved project as stored.
  const atEntry = useGoal(() => atGoalEntry())
  // Suggestions come only after an output exists - the expanded goal or an assistant reply - never before one (5 Nov 2026).
  const hasOutput = useAuiState(s => s.thread.messages.some(m => m.role === 'assistant')) || (hasDraft && !atEntry)
  const chips = useMemo(() => !chipDraft || running || !hasOutput ? [] : conversationChips(chipDraft, names ? names.split('\u0000') : [], since > 0 ? sentAll[sentAll.length - 1] : null),
    [chipDraft, running, hasOutput, names, since, userTexts])
  useEffect(() => { if (!empty) continueGoal() }, [empty]) // asking something from the fresh entry also continues the project
  const composer = <Composer capture={capture} chips={chips} />
  // The first message: the idle Orb and greeting fade out over the conversation as it fades in (~300ms, no snap).
  const [leaving, setLeaving] = useState(false)
  const wasEmpty = useRef(empty)
  useEffect(() => { if (wasEmpty.current && !empty) setLeaving(true); wasEmpty.current = empty }, [empty])
  useEffect(() => { if (!leaving) return; const t = setTimeout(() => setLeaving(false), 300); return () => clearTimeout(t) }, [leaving])
  return (
    <ThreadPrimitive.Root className="as-thread">
      {/* Files dropped on the pane attach to the chat (questions only), like "+". */}
      <ComposerPrimitive.AttachmentDropzone className="as-dropzone">
        {/* Upload, nothing said yet: the goal capture (29 Oct 2026) in place of the greeting. */}
        {empty && screenId === '1.1' ? <>
          {/* The goal box is this screen's chat (5 Nov 2026): no second composer or suggestions under the capture. */}
          <div className="as-viewport">{hasDraft && !atEntry ? <GoalExpansion /> : <GoalCapture />}</div>
          {hasDraft && !atEntry && composer}
        </> : empty && hasDraft && !atEntry ? <>
          {/* Continuity (5 Nov 2026): the expanded goal stays at the top of the chat on every screen. */}
          <div className="as-viewport"><GoalExpansion /></div>
          {composer}
        </> : empty ? <div className="as-empty">
          <IdleOrb />
          <h3 className="as-hello">How can I help you today?</h3>
          <p className="as-sub" role="status">{note}</p>
          {composer}
        </div> : <>
          {leaving && screenId !== '1.1' && <div className="as-empty as-empty-ghost" aria-hidden><IdleOrb paused /><h3 className="as-hello">How can I help you today?</h3></div>}
          {/* One persistent list: stage updates are messages in it (never a replaced note). Only live progress
              (a Verify run in flight, uploads in flight) sits after the messages until it finishes. */}
          <ThreadPrimitive.Viewport className={`as-viewport${leaving ? ' as-fade-in' : ''}`}>
            {hasDraft && !atEntry && <GoalExpansion />}
            <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, EditComposer }} />
            {screenId === '2.3' && <QualityProgress />}
            {/* Scrolled up to read history: a cue back to the newest message (assistant-ui disables it at the bottom). */}
            <ThreadPrimitive.ScrollToBottom className="as-jump"><Icon name="tb-arrow-down" size={14} />Jump to latest</ThreadPrimitive.ScrollToBottom>
          </ThreadPrimitive.Viewport>
          {composer}
        </>}
      </ComposerPrimitive.AttachmentDropzone>
    </ThreadPrimitive.Root>
  )
}

// Style-guide blues only: the accent, a light tint of it (the swirl blends these two, so it must differ enough to read),
// and a shadow tone. The Orb parses colour strings, not CSS vars.
const ORB = { light: { colors: ['#F2F9FD', '#A9BFF5', '#C5D0E1'], background: '#FFFFFF' }, dark: { colors: ['#5B83F2', '#9DB6F7', '#142540'], background: '#101E32' } }
/** The idle-assistant Orb above the greeting: slow, low hover lean, dimmed (in CSS: its own opacity greys a light background). */
function IdleOrb({ paused }: { paused?: boolean }) {
  const o = ORB[useUi(s => s.theme)]
  return <div className="as-orb" aria-hidden><Orb colors={o.colors} backgroundColor={o.background} speed={1.05} hoverIntensity={0.3} glow={0.8}
    size={1} rotateOnHover={false} clickPulse={false} intro={!paused} paused={paused} /></div>
}

/** Attachment cards, the rounded composer (+ · input · Dictate · Send / Stop), then the suggestion chips. */
function Composer({ capture, chips }: { capture: boolean; chips: string[] }) {
  return <div className="as-compose">
    <AuiIf condition={s => s.composer.attachments.length > 0}>
      <div className="as-files"><ComposerPrimitive.Attachments components={{ Attachment: ChatFile }} /></div>
    </AuiIf>
    {dictation && <DictatePill />}
    {/* Suggestions sit directly above the chatbox (29 Oct 2026). */}
    {chips.length > 0 && <div className="as-sugwrap">
      <span className="as-sugcap" id="as-sugcap">Suggested for this output</span>
      <div className="as-sugs" aria-labelledby="as-sugcap">
        {chips.map(c => <ThreadPrimitive.Suggestion key={c} className="as-sug" prompt={c} send>{c}</ThreadPrimitive.Suggestion>)}
      </div>
    </div>}
    <ComposerPrimitive.Root className="as-composer">
      <Tip text="Attach a file to ask about it">
        <ComposerPrimitive.AddAttachment className="as-attach" aria-label="Attach a file to ask about it"><Icon name="tb-plus" size={18} /></ComposerPrimitive.AddAttachment>
      </Tip>
      <label className="sr-only" htmlFor="as-input">{capture ? 'Intake objective' : 'Message the Enterprise Assistant'}</label>
      <div className="as-field">
        <ComposerPrimitive.Input id="as-input" className="as-input" rows={1} placeholder="Ask about this output..." />
        {dictation && <DictateListening />}
      </div>
      {dictation && <DictateControls />}
      <AuiIf condition={s => !s.thread.isRunning}>
        <ComposerPrimitive.Send className="as-send" aria-label="Send message"><Icon name="tb-send" size={16} /></ComposerPrimitive.Send>
      </AuiIf>
      <AuiIf condition={s => s.thread.isRunning}>
        <ComposerPrimitive.Cancel className="as-send as-stop" aria-label="Stop generating"><Icon name="tb-player-stop-filled" size={14} /></ComposerPrimitive.Cancel>
      </AuiIf>
    </ComposerPrimitive.Root>
    {dictation && <DictateNote />}
  </div>
}

function ChatFile() {
  const a = useAuiState(s => s.attachment)
  return <AttachmentPrimitive.Root className="as-file">
    <Icon name="tb-file-text" size={22} />
    <span className="as-file-meta"><strong><AttachmentPrimitive.Name /></strong>{a.file && <span>{fmtBytes(a.file.size)}</span>}</span>
    <AttachmentPrimitive.Remove className="as-file-x" aria-label={`Remove ${a.name}`}><Icon name="tb-x" size={14} /></AttachmentPrimitive.Remove>
  </AttachmentPrimitive.Root>
}
function SentFile() {
  const name = useAuiState(s => s.attachment.name)
  return <span className="as-sent-file"><Icon name="tb-file-text" size={14} />{name}</span>
}

// ---- Dictate: Idle · Listening · Transcribing · Draft ready · Microphone blocked · Error (components/dictation.ts) ----
function DictatePill() {
  const d = useDictate()
  if (d.phase !== 'transcribing') return null
  return <div className="as-dict-pill-row" aria-live="polite">
    <span className="as-dict-pill"><Icon name="tb-loader" size={12} spin />Transcribing…</span>
  </div>
}
function DictateControls() {
  const d = useDictate()
  // Escape stops a recording (the transcript stays as an editable draft), or cancels a pending microphone request.
  useEffect(() => {
    if (d.phase !== 'listening' && d.phase !== 'requesting') return
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); stopDictation() } }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [d.phase])
  if (d.phase === 'transcribing') return <button type="button" className="as-dictate busy" disabled aria-label="Transcribing"><Icon name="tb-loader" size={16} spin /></button>
  // Requesting (21 Oct 2026, round 2): disabled with a spinner until getUserMedia settles; Escape still cancels.
  if (d.phase === 'requesting') return <button type="button" className="as-dictate rec req" disabled aria-label="Requesting microphone...">
    <Icon name="tb-loader" size={16} spin /></button>
  // Listening (21 Oct 2026, listening variant 1): the mic button becomes the blue stop button with five equalizer bars.
  if (d.phase === 'listening') return <ComposerPrimitive.StopDictation className="as-dictate rec" aria-label="Stop recording">
    <span className="as-eq" aria-hidden>{[0, 1, 2, 3, 4].map(i => <i key={i} />)}</span>
  </ComposerPrimitive.StopDictation>
  // A blocked microphone returns the button to idle; the note under the composer says why (21 Oct 2026).
  return <ComposerPrimitive.Dictate className="as-dictate" aria-label="Dictate" aria-describedby={d.blocked ? 'as-dict-note' : undefined} data-tip="Dictate">
    <Icon name="tb-microphone" size={16} /></ComposerPrimitive.Dictate>
}
/** While the microphone is requested the input says so; while listening, it shows "Listening... speak now" with the timer at its right edge (it covers the input, same box). */
function DictateListening() {
  const d = useDictate()
  const [, tick] = useState(0)
  useEffect(() => { if (d.phase !== 'listening') return; const t = setInterval(() => tick(n => n + 1), 500); return () => clearInterval(t) }, [d.phase])
  if (d.phase === 'requesting') return <div className="as-listen"><span className="as-listen-text" role="status">Requesting microphone...</span></div>
  if (d.phase !== 'listening') return null
  return <div className="as-listen">
    <span className="as-listen-text" role="status">Listening... speak now</span>
    <span className="as-timer" aria-label="Recording time">{fmtClock(Date.now() - d.startedAt)}</span>
  </div>
}
function DictateNote() {
  const d = useDictate()
  if (d.phase !== 'idle') return null
  if (d.blocked) return <p className="as-dict-note" id="as-dict-note" role="status">Microphone blocked - allow access to dictate</p>
  if (d.error) return <p className="as-dict-note" role="status">{d.error}</p>
  return null
}

function UserMessage() {
  return <MessagePrimitive.Root className="as-urow">
    <MessagePrimitive.Attachments components={{ Attachment: SentFile }} />
    <div className="as-uline">
      <div className="as-msg as-user"><MessagePrimitive.Parts /></div>
    </div>
  </MessagePrimitive.Root>
}
/** Edit and resend: the bubble becomes an editable box; Send regenerates the reply that followed it. */
function EditComposer() {
  return <ComposerPrimitive.Root className="as-edit">
    <ComposerPrimitive.Input className="as-edit-input" aria-label="Edit message" autoFocus />
    <div className="as-edit-actions">
      <ComposerPrimitive.Cancel className="as-pill">Cancel</ComposerPrimitive.Cancel>
      <ComposerPrimitive.Send className="as-pill primary">Send</ComposerPrimitive.Send>
    </div>
  </ComposerPrimitive.Root>
}
/** Reply text with a blinking caret while it streams (assistant-ui's in-progress slot). */
const ReplyText = () => <p><MessagePartPrimitive.Text /><MessagePartPrimitive.InProgress><span className="as-caret" aria-hidden /></MessagePartPrimitive.InProgress></p>
const Generating = () => <span className="as-gen" role="status"><span className="as-dots" aria-hidden><i /><i /><i /></span>Assistant is generating...</span>
function AssistantMessage() {
  const status = useAuiState(({ message }) => message.status?.type)
  const empty = useAuiState(({ message }) => !message.content.some(p => p.type === 'text' && p.text))
  const custom = useAuiState(({ message }) => message.metadata?.custom) as { source?: string; failed?: boolean; confirm?: string } | undefined
  const pending = useContext(ConfirmCtx)
  const screen = useContext(ScreenCtx)
  const running = status === 'running'
  const avatar = <span className="as-avatar" aria-hidden><img src={bull} alt="" /></span>
  if (running && empty) return <MessagePrimitive.Root className="as-arow">{avatar}<div className="as-abody">{screen === '2.3' ? <Searching /> : <Generating />}</div></MessagePrimitive.Root>
  return <MessagePrimitive.Root className="as-arow">{avatar}<div className="as-abody">
    <div className={`as-msg as-bot${custom?.failed ? ' as-fail' : ''}`}><MessagePrimitive.Parts components={{ Text: ReplyText }} />{custom?.source && <Source label={custom.source} />}
    {custom?.confirm && custom.confirm === pending && <span className="as-confirm">
      {/* assistant-ui suggestions: each posts its answer as the user's reply (clearComposer off: inside a message it would target the edit composer, which an assistant message does not have). */}
      <ThreadPrimitive.Suggestion className="btn btn-primary" prompt="Yes" send clearComposer={false}>Yes</ThreadPrimitive.Suggestion>
      <ThreadPrimitive.Suggestion className="btn btn-secondary" prompt="No" send clearComposer={false}>No</ThreadPrimitive.Suggestion>
    </span>}</div>
    {/* No action bar under replies (29 Oct 2026, per user). */}
    {running && <Generating />}
  </div></MessagePrimitive.Root>
}

// ---- Context quality thread: derived from the same store as the Results & edits list, so the two never disagree.
const Searching = () => <span className="as-wait"><Icon name="spinner" size={13} spin />Searching the graph and checking the source</span>
const Source = ({ label }: { label: string }) => <span className="as-src"><Icon name="file" size={11} />{label}</span>
function QaPair({ q }: { q: Row }) {
  return <>
    <div className="as-msg as-user">{q.question}</div>
    {q.status === 'running' ? <div className="as-msg as-bot"><Searching /></div>
      : <div className={`as-msg as-bot${q.status === 'flagged' ? ' as-fail' : ''}`}>{q.answer}{q.status === 'passed' && q.source && <><br /><Source label={q.source} /></>}</div>}
  </>
}
/** Verify while a sample run is in flight: live progress after the messages. When the run ends it disappears and its
 *  result is posted as a message (see AssistantPane), so nothing that happened is lost. */
function QualityProgress() {
  const rows = useChecks() as Row[]
  const run = useRunState()
  // The sample results stay in the chat after the run finishes (5 Nov 2026 fix): they used to vanish the moment it ended.
  // They go only when the upstream changes and every row is reset to Untested (phase back to idle).
  if (run.phase === 'idle') return null
  const inRun = run.order.map(id => rows.find(r => r.id === id)).filter((r): r is Row => !!r)
  return <>
    <div className="as-msg as-bot" role="status">{run.running ? 'Running' : 'Ran'} the sample set. {inRun.filter(r => r.status === 'passed' || r.status === 'flagged').length} of {inRun.length} answered.</div>
    {inRun.filter(r => r.status !== 'pending').map(q => <QaPair key={q.id} q={q} />)}
  </>
}
