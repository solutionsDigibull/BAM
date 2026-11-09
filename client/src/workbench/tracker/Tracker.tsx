import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { goScreen, usePath } from '../lib/route'
import { Icon } from '../components/ui'
import {
  STATUS_LABEL, countedOf, screenByPath, screenLabel, stageOf, TASKS, TRACKER_ITEMS, TRACKER_STATUSES, doneCount, subtasksOf, taskStatus,
  type TrackerItem, type TrackerStatus,
} from './trackerConfig.ts'
import { setTracker, updateSubtask, useStatuses, useTrackerStore } from './useTrackerStore'
import { DONE_RULE } from './evidence.ts'
import { stageMarkers, type StageMarker } from '../lib/bamUi.ts'

// Shape differs per status, so status never relies on colour alone.
const GLYPH: Record<TrackerStatus, string> = {
  not_started: 'circle', in_progress: 'half', needs_review: 'eye', done: 'check', blocked: 'alert',
}

function StatusGlyph({ status }: { status: TrackerStatus }) {
  return <span className={`glyph s-${status}`} title={STATUS_LABEL[status]}><Icon name={GLYPH[status]} size={12} /><span className="sr-only">{STATUS_LABEL[status]}</span></span>
}

// One stage of the stepper: marker, name, status. The blue halo marks the execution-current stage (from evidence);
// the viewed stage only gets a subtle selection tint, so navigating never moves progress.
export function StageStep({ item, marker, earned, last, open, currentScreen, done, total, prev, onToggle, setRef, children }: {
  item: TrackerItem; marker: StageMarker; earned: boolean; last: boolean; open: boolean; currentScreen?: string
  done: number; total: number; prev: number; onToggle: () => void; setRef?: (el: HTMLButtonElement | null) => void; children?: ReactNode
}) {
  const { status, current } = marker
  const pos = currentScreen ? subtasksOf(item.id).findIndex(s => s.id === currentScreen) + 1 : 0
  const here = currentScreen ? screenLabel(currentScreen) : ''
  return (
    <li className={`bst s-${status} ${current ? 'current' : ''} ${pos ? 'viewing' : ''} ${open ? 'open' : ''}`} aria-current={current ? 'step' : undefined}>
      {!last && <span className={`bst-line ${earned ? 'earned' : ''}`} aria-hidden />}
      <button ref={setRef} id={`tracker-stage-${item.id}`} className="bst-btn" aria-expanded={open} aria-controls={`tracker-panel-${item.id}`} onClick={onToggle}
        aria-label={`${item.label}. ${STATUS_LABEL[status]}${current ? ', current stage' : ''}. ${done} of ${total} screens done.${pos ? ` You are viewing ${here}.` : ''}${prev ? ` ${prev} from a previous run.` : ''} Shows its screens.`}>
        {current && <span className="bst-chev" aria-hidden><Icon name="down" size={12} /></span>}
        <span className="bst-mark" aria-hidden>
          {status === 'done' ? <Icon name="check" size={14} /> : status === 'blocked' ? <Icon name="alert" size={13} /> : status === 'needs_review' ? <Icon name="eye" size={13} /> : <Icon name={item.icon} size={13} />}
        </span>
        <span className="bst-label" aria-hidden>{item.shortLabel}</span>
        <span className="bst-status" aria-hidden title={`${done} of ${total} screens done${prev ? `; ${prev} from a previous RAG run (not counted)` : ''}`}>
          {status === 'blocked' || status === 'needs_review' ? <Icon name={GLYPH[status]} size={11} /> : null}
          {STATUS_LABEL[status]}<span className="bst-count"> · {done}/{total}</span>
          {pos > 0 && <span className="bst-pos">at {here}</span>}
        </span>
      </button>
      {children}
    </li>
  )
}

// Selecting a screen shows its details and manual status; completion only changes through these controls.
function StatusMenu({ item }: { item: TrackerItem }) {
  const current = useStatuses()[item.id] ?? 'not_started'
  const loaded = useTrackerStore(t => t.loaded)
  const prev = useTrackerStore(t => t.previous.includes(item.id))
  const pinned = useTrackerStore(t => t.basis[item.id] !== undefined && t.basis[item.id] === t.evidence[item.id])
  return (
    <div className="status-menu">
      <div className="sm-head">
        <span className="sm-title"><Icon name={item.icon} size={13} /> {item.label}</span>
      </div>
      <p className="sm-desc">{item.description}</p>
      <p className="sm-rule"><Icon name="check" size={11} />{DONE_RULE[item.id]}{prev && ' Its current work is from a previous RAG run, so it counts as not started.'}{pinned && ' Set by hand; it returns to the evidence when the work changes.'}</p>
      <fieldset className="status-options" disabled={!loaded}>
        <legend className="sm-legend">Status{!loaded && ' (loading saved progress…)'} · updates from workflow evidence</legend>
        {TRACKER_STATUSES.map(st => (
          <label key={st} className={`status-opt s-${st} ${current === st ? 'on' : ''}`}>
            <input type="radio" name={`status-${item.id}`} value={st} checked={current === st} onChange={() => void updateSubtask(item.id, st)} />
            <Icon name={GLYPH[st]} size={12} />{STATUS_LABEL[st]}
          </label>
        ))}
      </fieldset>
    </div>
  )
}

function TrackerSubtaskRow({ stage, currentScreen }: { stage: TrackerItem; currentScreen?: string }) {
  const statuses = useStatuses()
  const selected = useTrackerStore(t => t.selected)
  const previous = useTrackerStore(t => t.previous)
  const picked = TRACKER_ITEMS.find(i => i.id === selected && i.parentId === stage.id)
  return (
    <section id={`tracker-panel-${stage.id}`} className="tracker-panel" aria-labelledby={`tracker-stage-${stage.id}`}>
      <div className="tracker-chips">
        {subtasksOf(stage.id).map(s => {
          const st = statuses[s.id] ?? 'not_started'
          const prev = previous.includes(s.id)
          return (
            <button key={s.id} data-screen={s.id} className={`chip s-${st} ${prev ? 'prev' : ''} ${selected === s.id ? 'on' : ''} ${currentScreen === s.id ? 'current' : ''}`}
              aria-pressed={selected === s.id} aria-current={currentScreen === s.id ? 'page' : undefined}
              title={`${STATUS_LABEL[st]}${prev ? ' (from previous run)' : ''}. ${DONE_RULE[s.id]}`}
              aria-label={`${s.label}${s.optional ? ' (optional)' : ''}. ${prev ? 'From previous run' : STATUS_LABEL[st]}. ${DONE_RULE[s.id]}`}
              onClick={() => { setTracker({ selected: selected === s.id ? null : s.id }); goScreen(s.id) }}>
              <span className="chip-num" aria-hidden><Icon name={s.icon} size={12} /></span>
              <span className="chip-label" aria-hidden>{s.label}</span>
              {prev ? <span className="chip-prev">from previous run</span> : <StatusGlyph status={st} />}
            </button>
          )
        })}
      </div>
      {picked && <StatusMenu item={picked} />}
    </section>
  )
}

// One row per stage (top = Data Ingestion): its line fills green with the stage's done screens; a finished stage's tick turns green.
function ProgressGlyph({ fill }: { fill: number[] }) {
  return (
    <svg className="wf-glyph" width="20" height="20" viewBox="0 0 20 20" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {fill.map((f, i) => {
        const y = 3 + i * 4.6, done = f >= 1
        return <g key={i} className={done ? 'done' : ''}>
          <path d={`M2 ${y}l1.3 1.3L6 ${y - 1.6}`} className="wf-tick" />
          <path d={`M9 ${y}h9`} className="wf-track" />
          {f > 0 && <path d={`M9 ${y}h${9 * Math.min(1, f)}`} className="wf-fill" />}
        </g>
      })}
    </svg>
  )
}

// The four stages behind one small top-bar icon. Hover or keyboard focus opens the panel (it overlays the screen,
// never pushes it down); click/tap pins it. Escape, an outside click or Close closes it and returns focus to the icon.
export function TrackerMenu() {
  const openTask = useTrackerStore(t => t.openTask)
  const selected = useTrackerStore(t => t.selected)
  const storageError = useTrackerStore(t => t.storageError)
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const icon = useRef<HTMLButtonElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const quiet = useRef(false) // focus returned by closing must not reopen
  const shown = screenByPath(usePath())?.item
  const currentScreen = shown?.navAs ?? shown?.id
  const currentStage = currentScreen ? stageOf(currentScreen)?.id : undefined
  const statuses = useStatuses()
  const previous = useTrackerStore(t => t.previous)
  const { markers, current } = stageMarkers(TASKS.map(t => taskStatus(t.id, statuses)))
  // Announce real workflow changes (current stage / its status) politely, not every poll.
  const announce = current < 0 ? (markers.every(m => m.status === 'done') ? 'All four stages done' : '') : `Current stage: ${TASKS[current].label}, ${STATUS_LABEL[markers[current].status]}`

  const show = () => { clearTimeout(timer.current); setOpen(true) }
  const close = (refocus: boolean) => {
    clearTimeout(timer.current); setOpen(false); setPinned(false)
    if (refocus) { quiet.current = true; icon.current?.focus(); quiet.current = false }
  }
  // Brief delay so crossing the gap between icon and panel never flickers it shut.
  const leave = () => {
    if (pinned || wrap.current?.contains(document.activeElement)) return
    clearTimeout(timer.current); timer.current = window.setTimeout(() => setOpen(false), 160)
  }
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => {
    if (!open) return
    const outside = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) close(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])

  // Opening a stage also opens its first built screen; closing it leaves you where you are.
  const toggle = (id: string) => {
    const opening = openTask !== id
    setTracker({ openTask: opening ? id : null, selected: null })
    if (opening && currentStage !== id) {
      const first = subtasksOf(id)[0]
      if (first) goScreen(first.id)
    }
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || !open) return
    if (selected) {
      // The detail (and any focused control in it) disappears; keep focus on the screen chip.
      document.querySelector<HTMLElement>(`.chip[data-screen="${selected}"]`)?.focus()
      setTracker({ selected: null })
    } else close(true)
  }

  return (
    <div className="wf" ref={wrap} onPointerEnter={show} onPointerLeave={leave} onKeyDown={onKey}
      onBlur={e => { if (!pinned && !wrap.current?.contains(e.relatedTarget as Node | null) && !wrap.current?.matches(':hover')) setOpen(false) }}>
      <button ref={icon} className={`icon-btn wf-icon ${open ? 'on' : ''}`} aria-label={`Workflow progress: ${markers.filter(m => m.status === 'done').length} of 4 stages done`} aria-expanded={open} aria-controls="wf-panel"
        title={`Workflow progress: ${markers.filter(m => m.status === 'done').length} of 4 stages done`} onFocus={() => { if (!quiet.current) show() }}
        onClick={() => { if (pinned) close(false); else { setPinned(true); show() } }}>
        <ProgressGlyph fill={TASKS.map(t => { const s = countedOf(t.id); return s.length ? doneCount(s, statuses) / s.length : 0 })} />
      </button>
      <span className="sr-only" aria-live="polite">{announce}</span>
      <div id="wf-panel" className="wf-panel" role="region" aria-label="Workflow progress" hidden={!open}>
        <div className="wf-head">
          <div><strong>Workflow</strong>{currentScreen && <span className="muted xs">Current screen · {screenLabel(currentScreen)}</span>}</div>
          <button className="icon-btn sm" aria-label="Close workflow progress" onClick={() => close(true)}><Icon name="x" size={12} /></button>
        </div>
        {storageError && <p className="tracker-error"><Icon name="alert" size={12} />{storageError} Progress is kept on this device only, not shared or synced.</p>}
        <ol className="wf-stages">
          {TASKS.map((t, i) => (
            <StageStep key={t.id} item={t} marker={markers[i]} earned={false} last open={openTask === t.id}
              currentScreen={currentStage === t.id ? currentScreen : undefined}
              done={doneCount(countedOf(t.id), statuses)} total={countedOf(t.id).length} prev={countedOf(t.id).filter(s => previous.includes(s.id)).length}
              onToggle={() => toggle(t.id)}>
              {openTask === t.id && <TrackerSubtaskRow stage={t} currentScreen={currentScreen} />}
            </StageStep>
          ))}
        </ol>
      </div>
    </div>
  )
}
