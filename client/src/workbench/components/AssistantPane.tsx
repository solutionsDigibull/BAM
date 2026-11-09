// Enterprise Assistant (middle pane), built on assistant-ui primitives with a local runtime. The thread lives for the
// whole session and is never keyed by screen, so navigating keeps the conversation. The pane is only the thread and the
// composer (input, Dictate, send). The chat holds no unprompted status notes: an assistant message only answers something
// the person asked; the live screen note sits in the empty thread's greeting instead. Replies come from the live project
// snapshot (lib/assistant.ts); nothing here approves, certifies, hands over or deploys. No language model yet (#729).
// Chat layout: ChatGPT-style thread. Empty = greeting + composer + starter chips; then accent user bubbles (editable;
// edit + resend regenerates), assistant replies beside a "B" avatar, streamed with Stop. "+" attaches files to the chat
// for questions only: they are never uploaded or ingested (project files go in through + Add files on the left).
import './assistant.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { AssistantRuntimeProvider, AttachmentPrimitive, AuiIf, ComposerPrimitive, MessagePartPrimitive, MessagePrimitive, ThreadPrimitive, useAuiState, useLocalRuntime,
  type AttachmentAdapter, type ChatModelAdapter, type ExportedMessageRepository, type ThreadMessage } from '@assistant-ui/react'
import { createDictation, fmtClock, stopDictation, useDictate } from './dictation'
import type { Snapshot } from '../api/types'
import { STARTER_CHIPS, attachmentReply, reply, screenNote, type AssistantFacts } from '../lib/assistant'
import { prerequisite } from '../lib/prereq'
import { activeScreen } from '../lib/route'
import { setUi, useUi } from '../lib/store'
import { relations, useRag } from '../rag/ragPipeline'
import { STATUS_LABEL, TASKS, countedOf, doneCount, screenLabel, stageOf, taskStatus } from '../tracker/trackerConfig'
import { useStatuses } from '../tracker/useTrackerStore'
import { fileCounts, useUploadRows } from '../steps/UploadPanes'
import { GoalCapture, GoalExpansion } from '../steps/GoalPanes'
import { useGoal } from '../lib/goal'
import { Icon, Tip, fmtBytes } from './ui'
import Orb from './reactbits/Orb'
import bull from '../assets/login/bull-head-transparent.png'

const dictation = createDictation()
// Chat attachments stay in the thread (questions about a file); nothing is uploaded or ingested.
const chatFiles: AttachmentAdapter = {
  accept: '*',
  async add({ file }) { return { id: crypto.randomUUID(), type: 'file', name: file.name, contentType: file.type, file, status: { type: 'requires-action', reason: 'composer-send' } } },
  async send(a) { return { ...a, status: { type: 'complete' }, content: [] } },
  async remove() {},
}

// ---- one persistent thread per project ------------------------------------------------------------------------
// The runtime lives in AssistantPane, which the shell mounts once for the session (never keyed by screen), so navigating
// never resets the thread. History is text only, kept in memory and in sessionStorage so a reload keeps it too.
const projectOf = () => (typeof window === 'undefined' ? undefined : /\/projects\/([^/]+)/.exec(window.location.pathname)?.[1]) ?? 'default'
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

export function AssistantPane({ snap, screenId, locked }: { snap: Snapshot; screenId?: string; locked?: string }) {
  const collapsed = useUi(s => s.shell.assistant)
  const ask = useUi(s => s.assistantAsk)
  const rag = useRag(s => s)
  const statuses = useStatuses()
  const counts = fileCounts(useUploadRows(snap))
  const facts: AssistantFacts = {
    screen: screenId ? { id: screenId, label: screenLabel(screenId), stage: stageOf(screenId)?.label ?? '', locked } : undefined,
    snap,
    blocker: prerequisite(snap, rag, 4),
    stages: TASKS.map(t => ({ label: t.label, status: STATUS_LABEL[taskStatus(t.id, statuses)], done: doneCount(countedOf(t.id), statuses), total: countedOf(t.id).length })),
    active: screenLabel(activeScreen(snap)),
    statuses,
    files: { total: counts.totalFiles, uploaded: counts.uploadedCount },
    rag: { runId: rag.runId, steps: rag.steps.map(s => ({ label: s.label, status: s.status })), relations: relations(rag), nodes: rag.graphData?.nodes.length },
  }
  // The adapter is created once (stable runtime); it reads the latest facts through a ref.
  const latest = useRef(facts)
  useEffect(() => { latest.current = facts })
  const adapter = useMemo<ChatModelAdapter>(() => {
    type Answer = { content: [{ type: 'text'; text: string }] }
    const answer = (messages: readonly ThreadMessage[]): Answer => {
      const last = messages[messages.length - 1]
      const text = (last?.content.map(p => (p.type === 'text' ? p.text : '')).join(' ') ?? '').trim()
      // Chat attachments are for questions only (never ingested); no model reads them yet, so say so.
      const files = last?.role === 'user' ? last.attachments.map(a => a.name) : []
      if (files.length) return { content: [{ type: 'text', text: attachmentReply(files) }] }
      return { content: [{ type: 'text', text: reply(text, latest.current) }] }
    }
    return {
      // The reply streams in (blinking cursor); Stop aborts it and keeps what has arrived.
      async *run({ messages, abortSignal }) {
        const a = answer(messages)
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
  const project = projectOf()
  // The pane unmounts whenever the shell shows its loading / error / redirect screen (snapshot refetch, route redirect),
  // which would throw the runtime and the whole chat away. The thread is therefore restored from the module store on
  // mount and saved there after every change (not while a reply streams), and also kept in sessionStorage.
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
  const note = screenNote(facts)
  const toggle = () => setUi(s => ({ shell: { ...s.shell, assistant: !s.shell.assistant } }))

  // Hidden by the person only (Hide assistant); a stage change never hides it. The runtime provider stays mounted, so
  // the thread keeps its messages while the pane is hidden.
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
      <aside className="shell-pane assist" aria-label="BuLLM Assistant" data-screen={screenId}>
        <header className="shell-head">
          <button className="btn btn-ghost sm as-hide" onClick={toggle} aria-expanded aria-label="Hide assistant"><Icon name="panel-left" size={14} />Hide assistant</button>
        </header>
        <ThreadBody note={note} screenId={screenId} />
      </aside>
    </AssistantRuntimeProvider>
  )
}

/** Empty: the greeting with the composer and starter chips centred. Otherwise the thread, then the composer. */
function ThreadBody({ note, screenId }: { note: string; screenId?: string }) {
  const empty = useAuiState(s => s.thread.messages.length === 0)
  const saved = useGoal(s => s.saved), hasGoal = useGoal(s => !!s.text)
  // The first message: the idle Orb and greeting fade out over the conversation as it fades in (~300ms, no snap).
  const [leaving, setLeaving] = useState(false)
  const wasEmpty = useRef(empty)
  useEffect(() => { if (wasEmpty.current && !empty) setLeaving(true); wasEmpty.current = empty }, [empty])
  useEffect(() => { if (!leaving) return; const t = setTimeout(() => setLeaving(false), 300); return () => clearTimeout(t) }, [leaving])
  return (
    <ThreadPrimitive.Root className="as-thread">
      {/* Files dropped on the pane attach to the chat (questions only), like "+". */}
      <ComposerPrimitive.AttachmentDropzone className="as-dropzone">
        {/* Upload, no goal yet: the goal box in place of the greeting. It is this screen's chat: no second composer under it. */}
        {empty && screenId === '1.1' && !saved ? <div className="as-viewport"><GoalCapture /></div>
        : empty && hasGoal ? <>
          <div className="as-viewport"><GoalExpansion /></div>
          <Composer chips={STARTER_CHIPS} />
        </> : empty ? <div className="as-empty">
          <IdleOrb />
          <h3 className="as-hello">How can I help you today?</h3>
          <p className="as-sub" role="status">{note}</p>
          <Composer chips={STARTER_CHIPS} />
        </div> : <>
          {leaving && <div className="as-empty as-empty-ghost" aria-hidden><IdleOrb paused /><h3 className="as-hello">How can I help you today?</h3></div>}
          <ThreadPrimitive.Viewport className={`as-viewport${leaving ? ' as-fade-in' : ''}`}>
            {hasGoal && <GoalExpansion />}
            <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, EditComposer }} />
            {/* Scrolled up to read history: a cue back to the newest message (assistant-ui disables it at the bottom). */}
            <ThreadPrimitive.ScrollToBottom className="as-jump"><Icon name="tb-arrow-down" size={14} />Jump to latest</ThreadPrimitive.ScrollToBottom>
          </ThreadPrimitive.Viewport>
          <Composer chips={[]} />
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
function Composer({ chips }: { chips: readonly string[] }) {
  return <div className="as-compose">
    <AuiIf condition={s => s.composer.attachments.length > 0}>
      <div className="as-files"><ComposerPrimitive.Attachments components={{ Attachment: ChatFile }} /></div>
    </AuiIf>
    {dictation && <DictatePill />}
    {/* Suggestions sit directly above the chatbox. */}
    {chips.length > 0 && <div className="as-sugwrap">
      <span className="as-sugcap" id="as-sugcap">Try asking</span>
      <div className="as-sugs" aria-labelledby="as-sugcap">
        {chips.map(c => <ThreadPrimitive.Suggestion key={c} className="as-sug" prompt={c} send>{c}</ThreadPrimitive.Suggestion>)}
      </div>
    </div>}
    <ComposerPrimitive.Root className="as-composer">
      <Tip text="Attach a file to ask about it">
        <ComposerPrimitive.AddAttachment className="as-attach" aria-label="Attach a file to ask about it"><Icon name="tb-plus" size={18} /></ComposerPrimitive.AddAttachment>
      </Tip>
      <label className="sr-only" htmlFor="as-input">Message the Enterprise Assistant</label>
      <div className="as-field">
        <ComposerPrimitive.Input id="as-input" className="as-input" rows={1} placeholder="Ask about this project..." />
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
  // Requesting: disabled with a spinner until getUserMedia settles; Escape still cancels.
  if (d.phase === 'requesting') return <button type="button" className="as-dictate rec req" disabled aria-label="Requesting microphone...">
    <Icon name="tb-loader" size={16} spin /></button>
  // Listening: the mic button becomes the blue stop button with five equalizer bars.
  if (d.phase === 'listening') return <ComposerPrimitive.StopDictation className="as-dictate rec" aria-label="Stop recording">
    <span className="as-eq" aria-hidden>{[0, 1, 2, 3, 4].map(i => <i key={i} />)}</span>
  </ComposerPrimitive.StopDictation>
  // A blocked microphone returns the button to idle; the note under the composer says why.
  return <ComposerPrimitive.Dictate className="as-dictate" aria-label="Dictate" aria-describedby={d.blocked ? 'as-dict-note' : undefined} data-tip="Dictate">
    <Icon name="tb-microphone" size={16} /></ComposerPrimitive.Dictate>
}
/** While the microphone is requested the input says so; while listening, it shows "Listening... speak now" with the timer at its right edge. */
function DictateListening() {
  const d = useDictate()
  const [now, setNow] = useState(Date.now)
  useEffect(() => { if (d.phase !== 'listening') return; setNow(Date.now()); const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t) }, [d.phase])
  if (d.phase === 'requesting') return <div className="as-listen"><span className="as-listen-text" role="status">Requesting microphone...</span></div>
  if (d.phase !== 'listening') return null
  return <div className="as-listen">
    <span className="as-listen-text" role="status">Listening... speak now</span>
    <span className="as-timer" aria-label="Recording time">{fmtClock(now - d.startedAt)}</span>
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
  const running = status === 'running'
  const avatar = <span className="as-avatar" aria-hidden><img src={bull.src} alt="" /></span>
  if (running && empty) return <MessagePrimitive.Root className="as-arow">{avatar}<div className="as-abody"><Generating /></div></MessagePrimitive.Root>
  return <MessagePrimitive.Root className="as-arow">{avatar}<div className="as-abody">
    <div className="as-msg as-bot"><MessagePrimitive.Parts components={{ Text: ReplyText }} /></div>
    {running && <Generating />}
  </div></MessagePrimitive.Root>
}
