import { useEffect, useRef, useState } from 'react'
import { EditorContent, useEditor, useEditorState, type Editor } from '@tiptap/react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/adapter'
import type { Sow } from '../api/types'
import type { StepProps } from '../Workbench'
import { useAction } from '../lib/data'
import { useUi } from '../lib/store'
import { goScreen, goStep } from '../lib/route'
import { isRunning, ragPipeline, useRag } from '../rag/ragPipeline'
import { ChangeHighlight, DiffLegend, SowMeta, setChangeBase, sowExtensions, useSowDiff } from '../components/sow'
import { editCount } from '../lib/sowDiff'
import { sowComparison, type CompareWith } from '../lib/sowCompare'
import { Banner, Empty, ErrorState, GateButton, Icon, Pane, Skeleton, StatusPill, fmtTime } from '../components/ui'
import { tips } from '../lib/actionTips'
import { VisualsPanel } from './Visuals'
import './visuals.css'


export function Toolbar({ editor, label = 'SOW formatting' }: { editor: Editor; label?: string }) {
  const st = useEditorState({ editor, selector: ({ editor: e }) => ({
    bold: e.isActive('bold'), italic: e.isActive('italic'), h2: e.isActive('heading', { level: 2 }), h3: e.isActive('heading', { level: 3 }),
    ul: e.isActive('bulletList'), ol: e.isActive('orderedList'), undo: e.can().undo(), redo: e.can().redo(), editable: e.isEditable,
  }) })
  const b = (label: string, text: string, on: boolean | undefined, fn: () => void, disabled = false) => (
    <button className="tb" aria-label={label} title={label} aria-pressed={on} disabled={!st.editable || disabled} onClick={fn}>{text}</button>
  )
  return (
    <div className="toolbar" role="toolbar" aria-label={label}>
      {b('Bold', 'B', st.bold, () => editor.chain().focus().toggleBold().run())}
      {b('Italic', 'I', st.italic, () => editor.chain().focus().toggleItalic().run())}
      <span className="tb-sep" />
      {b('Heading 2', 'H2', st.h2, () => editor.chain().focus().toggleHeading({ level: 2 }).run())}
      {b('Heading 3', 'H3', st.h3, () => editor.chain().focus().toggleHeading({ level: 3 }).run())}
      {b('Bullet list', '•', st.ul, () => editor.chain().focus().toggleBulletList().run())}
      {b('Numbered list', '1.', st.ol, () => editor.chain().focus().toggleOrderedList().run())}
      <span className="tb-sep" />
      {b('Undo', '↶', undefined, () => editor.chain().focus().undo().run(), !st.undo)}
      {b('Redo', '↷', undefined, () => editor.chain().focus().redo().run(), !st.redo)}
    </div>
  )
}

type SaveState = 'idle' | 'pending' | 'saving' | 'saved' | 'error'

function SowEditor({ sow, editable, info }: { sow: Sow; editable: boolean; info: StepProps['info'] }) {
  const qc = useQueryClient()
  const base = useRef(sow.version)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [save, setSave] = useState<{ s: SaveState; at?: string; err?: string }>({ s: 'idle' })
  const [showDiff, setShowDiff] = useState(true)
  const [compareWith, setCompareWith] = useState<CompareWith>('previous')
  const [liveHtml, setLiveHtml] = useState(sow.html)
  const { busy, run } = useAction()

  const editor = useEditor({
    extensions: [...sowExtensions, ChangeHighlight], content: sow.html, editable,
    onUpdate: ({ editor: e }) => {
      setLiveHtml(e.getHTML())
      clearTimeout(timer.current)
      setSave({ s: 'pending' })
      timer.current = setTimeout(async () => {
        setSave({ s: 'saving' })
        try {
          base.current = await api.saveSowDraft(e.getHTML(), base.current)
          setSave({ s: 'saved', at: new Date().toISOString() })
          void qc.invalidateQueries()
        } catch (err) { setSave({ s: 'error', err: (err as Error).message }) }
      }, 800)
    },
  })
  useEffect(() => () => clearTimeout(timer.current), [])
  // emitUpdate=false: toggling editability is not an edit and must not autosave a new version.
  useEffect(() => { editor?.setEditable(editable, false) }, [editor, editable])
  // A new version from the backend (regeneration) replaces the content; our own saves already match.
  useEffect(() => {
    if (!editor || sow.version === base.current || save.s === 'pending' || save.s === 'saving') return
    editor.commands.setContent(sow.html, { emitUpdate: false })
    setLiveHtml(sow.html)
    base.current = sow.version
  }, [editor, sow.version, sow.html, save.s])

  // Accuracy findings (right pane) jump to their requirement in the document.
  const passage = useUi(s => s.passage)
  useEffect(() => {
    const root = editor?.view.dom
    if (!root || !passage) return
    root.querySelectorAll('.passage-hl').forEach(el => el.classList.remove('passage-hl'))
    const el = root.querySelector(`[data-passage="${CSS.escape(passage)}"]`)
    if (el) { el.classList.add('passage-hl'); el.scrollIntoView({ block: 'center', behavior: 'smooth' }) }
  }, [editor, passage])

  const saving = save.s === 'pending' || save.s === 'saving'
  const approved = sow.status === 'approved'
  const decideReason = !info.editable ? 'This SOW is read-only. Reopen it to change the decision.'
    : sow.status === 'regenerating' ? 'The SOW is being regenerated'
    : saving ? 'Saving your edits first' : save.s === 'error' ? 'Your last edit was not saved' : undefined
  const approveReason = decideReason ?? (approved ? `v${sow.version} is already approved` : undefined)
  // Default base: the previous saved version (or the saved version itself while edits are unsaved).
  // Audit base: the model-generated version.
  const cmp = sowComparison(sow, liveHtml, compareWith)
  const baseHtml = cmp.kind === 'diff' ? cmp.html : liveHtml
  const diff = useSowDiff(baseHtml, liveHtml)
  const edits = cmp.kind === 'diff' ? editCount(diff.counts) : 0
  // Colour-code changes live in the editor against the chosen base (display-only, never saved).
  useEffect(() => { if (editor) setChangeBase(editor, showDiff && cmp.kind === 'diff' ? baseHtml : null) }, [editor, showDiff, baseHtml, cmp.kind])
  const editsText = cmp.kind === 'none' ? 'First version: nothing to compare' : `${edits ? `${edits} change${edits === 1 ? '' : 's'}` : 'No changes'} · ${cmp.from} → ${cmp.to}`

  return (
    <Pane id="center-title" kicker="SOW review and FDE approval" title="Scope" className="center"
      actions={<span className="row">
        <label className="switch" title="Colour-code changes in the document">
          <input type="checkbox" role="switch" checked={showDiff} onChange={e => setShowDiff(e.target.checked)} />
          <span className="switch-track" aria-hidden><span /></span>Changes
        </label>
        <label className="compare-select">
          <span className="sr-only">Compare with</span>
          <select value={compareWith} onChange={e => setCompareWith(e.target.value as CompareWith)} aria-label="Compare with">
            <option value="previous">vs previous saved version</option>
            <option value="generated">vs model-generated (audit)</option>
          </select>
        </label>
        <span className={`edits-chip ${edits ? 'has' : ''}`}>{editsText}</span>
        <StatusPill status={approved ? 'done' : sow.status === 'regenerating' ? 'running' : 'needs_review'}
        text={approved ? `Approved v${sow.approval!.version}` : sow.status === 'regenerating' ? 'Regenerating' : `Draft v${sow.version}`} /></span>}
      footer={<>
        <div className="foot-info">
          <span className="mono">v{sow.version}</span>
          <span className="muted sm" role="status">
            {save.s === 'pending' ? 'Unsaved changes…' : save.s === 'saving' ? 'Saving draft…' : save.s === 'saved' ? `Draft saved ${fmtTime(save.at)}`
              : save.s === 'error' ? `Not saved: ${save.err}` : `Updated ${fmtTime(sow.updatedAt)}`}
            {cmp.kind === 'diff' && edits > 0 && ` · ${edits} change${edits === 1 ? '' : 's'}, ${cmp.from} → ${cmp.to} (${diff.counts.added} added, ${diff.counts.removed} removed, ${diff.counts.modified} modified)`}
          </span>
          {!approved && !decideReason && <span className="muted sm">Approving v{sow.version} unlocks the Grid and extracts skills from this exact version.</span>}
        </div>
        <div className="row">
          {/* Reopened with a still-valid approval: Approve keeps it and closes the review (no second approval). */}
          {approved && info.editable
            ? <GateButton action="approve" {...tips('3.1', 'Approve')} reason={decideReason} busy={busy === 'Close SOW review'}
                onClick={() => run('Close SOW review', () => api.closeReopen(1), { step: 1 }).then(() => goStep(2))}>Approve</GateButton>
            : <GateButton action="approve" {...tips('3.1', 'Approve')} reason={approveReason} busy={busy === 'Approve'} successKey={sow.approval?.version}
                onClick={() => run('Approve', () => api.approveSow(sow.version), { success: `SOW v${sow.version} approved. Skills extraction started in 3.2.`, step: 2 }).then(() => goStep(2))}>Approve</GateButton>}
        </div>
      </>}>
      <div className="banners">
        {approved && !info.editable && <Banner tone="ok" icon="check">Approved <strong>v{sow.approval!.version}</strong> at {fmtTime(sow.approval!.at)} by {sow.approval!.by}. Approval is tied to this version.</Banner>}
        {approved && info.editable && <Banner tone="warn" icon="alert">Approval of <strong>v{sow.approval!.version}</strong> is still valid. Any edit creates a new version and invalidates it.</Banner>}
        {sow.invalidated && <Banner tone="warn" icon="alert"><strong>Approval of v{sow.invalidated.version} invalidated</strong> at {fmtTime(sow.invalidated.at)}: {sow.invalidated.reason}.</Banner>}
        {sow.status === 'regenerating' && sow.changeRequest && <Banner tone="info" icon="spinner">Regenerating v{sow.changeRequest.version} with AI: “{sow.changeRequest.note}”. The new version appears here when the model finishes (see pipeline).</Banner>}
      </div>
      <SowMeta sow={sow} />
      {showDiff && cmp.kind === 'none' && <Banner tone="info" icon="question"><strong>No comparison yet.</strong> {cmp.reason}</Banner>}
      <div className="editor-bar">
        {editor && <Toolbar editor={editor} />}
        {showDiff && cmp.kind === 'diff' && <DiffLegend from={cmp.from} to={cmp.to} counts={diff.counts} />}
      </div>
      <div className={`sow-doc ${editable ? 'editable' : 'readonly'} ${showDiff ? 'tracking' : ''}`}><EditorContent editor={editor} aria-label="SOW document" /></div>
      <VisualsPanel />
    </Pane>
  )
}

export function Center({ snap, info }: StepProps) {
  const { busy, run } = useAction()
  const ragRunning = useRag(isRunning)
  const sow = snap.sow
  if (sow) return <SowEditor sow={sow} editable={info.editable && sow.status !== 'regenerating'} info={info} />
  const failed = snap.sowGeneration.find(s => s.status === 'failed')
  const running = snap.sowGeneration.some(s => s.status === 'running' || s.status === 'queued')
  return (
    <Pane id="center-title" kicker="SOW review and FDE approval" title="Scope" className="center">
      {failed ? <ErrorState title={`${failed.label} failed`} action={info.editable &&
          <button className="btn btn-primary" disabled={!!busy} onClick={() => run('Retry SOW generation', () => api.startSowGeneration(ragPipeline.getState().runId), { step: 1 })}><Icon name="refresh" />Retry SOW generation</button>}>
          {failed.error}
        </ErrorState>
        : running ? <><Banner tone="info" icon="spinner">Generating the SOW from the RAG context. It appears here when generation finishes; you can keep working meanwhile.</Banner><Skeleton rows={9} label="Generating SOW" /></>
        : !snap.uploads.length ? <Empty icon="upload" title="No SOW yet" action={<button className="btn btn-primary" onClick={() => goScreen('1.1')}><Icon name="upload" />Go to Upload</button>}>
            Upload documents in Ingestion. The RAG pipeline builds the context, and the SOW is generated from it here.
          </Empty>
        : <Empty icon="clock" title="Ready for SOW synthesis" action={
            <div className="row" style={{ gap: '8px' }}>
              <button className="btn btn-primary" disabled={!!busy} onClick={() => run('Generate SOW', () => api.startSowGeneration(ragPipeline.getState().runId), { step: 1 })}>
                <Icon name="bolt" />Generate SOW with Certified Context
              </button>
            </div>
          }>
            {ragRunning ? 'The RAG pipeline is running. Click Generate SOW once verified.' : 'Context graph and pre-flight checks are certified. Synthesize the SOW draft from the discovery context.'}
          </Empty>}
    </Pane>
  )
}




export { Intake } from './Intake'
