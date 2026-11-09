import { useEffect, useRef, useState } from 'react'
import { useUppyState } from '@uppy/react'
import type { Snapshot } from '../api/types'
import { setUi, uppy, useUi } from '../lib/store'
import { goScreen, navigate, usePath } from '../lib/route'
import { GLOBAL_ROUTES, STATUS_LABEL, SUBTASKS, TASKS, globalByPath, screenByPath, screenLabel, stageOf, subtasksOf, taskStatus, type TrackerStatus } from '../tracker/trackerConfig.ts'
import { useStatuses } from '../tracker/useTrackerStore'
import { signOut, useSession } from '../features/login/session'
import { AutoChip, HitlChip, Icon, Tip, fmtFull, fmtTime } from './ui'
import { BrandLockup } from './BrandLockup'
import { UploadFilesBlock } from '../steps/UploadPanes'

export const useUploadsInFlight = () =>
  useUppyState(uppy, s => Object.values(s.files).filter(f => f.progress.uploadStarted && !f.progress.uploadComplete && !f.error).length)

/** The highlighted navigation entry for a path (a hidden screen highlights its parent entry). */
export const navScreen = (path: string) => { const s = screenByPath(path)?.item; return s?.navAs ?? s?.id }

export function TopBar({ syncedAt, offline }: { snap?: Snapshot; syncedAt: number; offline: boolean }) {
  const path = usePath()
  const id = screenByPath(path)?.id
  const global = globalByPath(path)
  const theme = useUi(s => s.theme)
  const env = useUi(s => s.env)
  // Breadcrumb: Stage › Step (Admin › Masters keeps the workspace name).
  const first = global === 'admin' ? 'Admin' : id ? stageOf(id)?.label ?? '' : env === 'FDE' ? 'FDE workspace' : 'Staging'
  const current = global ? GLOBAL_ROUTES[global].label : id ? screenLabel(id) : ''
  return (
    <header className="topbar">
      <div className="brand">
        <button className="icon-btn nav-drawer-btn" onClick={() => setUi(s => ({ drawer: s.drawer === 'nav' ? null : 'nav' }))} aria-label="Open navigation" title="Navigation"><Icon name="menu" /></button>
        <BrandLockup />
        {/* Each crumb can shrink and ellipsize; the full text is in its tooltip. The screen name keeps priority. */}
        <nav aria-label="Breadcrumb" className="crumbs">
          <button className="link crumb-link crumb" onClick={() => navigate('/')} title={first}>{first}</button>
          {current && <><Icon name="chevron" size={12} /><strong className="crumb crumb-current" title={current} aria-current="page">{current}</strong></>}
        </nav>
      </div>
      <div className="topbar-right">
        <span className={`sync ${offline ? 'tone-err' : 'tone-muted'}`} role="status" title={syncedAt ? `Last backend sync ${fmtFull(new Date(syncedAt).toISOString())}` : undefined}>
          <Icon name={offline ? 'alert' : 'db'} size={12} />
          <span className="sync-text">{offline ? `Backend unreachable. Status unknown since ${syncedAt ? fmtTime(new Date(syncedAt).toISOString()) : 'start'}` : `All changes saved · ${syncedAt ? fmtTime(new Date(syncedAt).toISOString()) : '—'}`}</span>
        </span>
        {/* Glass mode switch: native radios (Staging = left half, FDE = right half); the sphere sits over the inactive half. */}
        <fieldset className="env-glass" data-env={env} aria-describedby="env-hint" title="View context, not a deploy">
          <legend className="sr-only">Environment mode</legend>
          <span className="eg-track" aria-hidden />
          <span className="eg-sphere" aria-hidden>
            <span className="eg-ghost">{env === 'FDE' ? 'Staging' : 'FDE'}</span>
            {env === 'FDE'
              ? <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5M9 3 7 13" /></svg>
              : <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 1.8 14.5 5 8 8.2 1.5 5z" /><path d="m3.2 7.6 4.8 2.4 4.8-2.4 1.7.85L8 11.6 1.5 8.45z" opacity=".85" /><path d="m3.2 10.6 4.8 2.4 4.8-2.4 1.7.85L8 14.6 1.5 11.45z" opacity=".7" /></svg>}
          </span>
          <span className="eg-text" aria-hidden>{env}</span>
          {(['Staging', 'FDE'] as const).map(e => (
            <label key={e} className={`eg-opt eg-${e.toLowerCase()} ${env === e ? 'on' : ''}`}>
              <input type="radio" name="env" value={e} checked={env === e} aria-label={e}
                onChange={() => setUi({ env: e })} />
            </label>
          ))}
        </fieldset>
        <span id="env-hint" className="sr-only">View context, not a deploy</span>
        <button className="icon-btn" aria-pressed={theme === 'light'} onClick={() => setUi({ theme: theme === 'dark' ? 'light' : 'dark' })}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
        </button>
        <ProfileMenu />
        <button className="icon-btn assist-drawer-btn" onClick={() => setUi(s => ({ drawer: s.drawer === 'assistant' ? null : 'assistant' }))} aria-label="Open BuLLM Assistant" title="BuLLM Assistant"><Icon name="sparkle" /></button>
      </div>
    </header>
  )
}

// Profile: who is signed in, Switch to Masters, and Sign out. Click or keyboard opens it; Escape or an outside click closes it.
function ProfileMenu() {
  const email = useSession()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const outside = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  const close = () => { setOpen(false); btn.current?.focus() }
  return (
    <div className="profile" ref={wrap} onKeyDown={e => { if (e.key === 'Escape' && open) close() }}>
      <button ref={btn} className="icon-btn" aria-expanded={open} aria-controls="profile-menu" aria-label="Profile" title="Profile" onClick={() => setOpen(o => !o)}>
        <Icon name="user" />
      </button>
      <div id="profile-menu" className="profile-menu" hidden={!open}>
        <p className="profile-who"><span className="muted xs">Signed in as</span><strong>{email ?? '—'}</strong></p>
        <button className="profile-item" onClick={() => { setOpen(false); signOut() }}><Icon name="logout" size={14} />Sign out</button>
      </div>
    </div>
  )
}

const STAGE_TEXT: Record<TrackerStatus, string> = { not_started: 'Waiting', in_progress: 'In progress', needs_review: 'Needs review', done: 'Done', blocked: 'Blocked' }

/** NAVIGATE: the four workflow stages and their screens (icon + label, no numbers). The only workflow navigation. */
/** A Workbench / Validation step's state in the rail: Ready (dot) · Running (spinner) · Failed. Done is the check.
 *  Needs review shows no mark and locked steps no lock icon (per user, 5 Nov 2026); the lock reason stays in the tooltip. */
function StepMark({ st }: { st: TrackerStatus }) {
  if (st === 'needs_review') return null
  const [icon, cls, label] = st === 'in_progress' ? ['spinner', 'nv-st-run', 'Running']
    : st === 'blocked' ? ['alert', 'nv-st-fail', 'Failed'] : ['dot', 'nv-st-ready', 'Ready']
  return <span className={`nv-st ${cls}`} title={label} aria-hidden><Icon name={icon} size={11} spin={icon === 'spinner'} /></span>
}

export function NavRail({ snap }: { snap?: Snapshot }) {
  const path = usePath()
  const current = navScreen(path)
  const currentStage = current ? stageOf(current)?.id : undefined
  const statuses = useStatuses()
  const collapsed = useUi(s => s.shell.nav)
  const [open, setOpen] = useState<Set<string>>(() => new Set(currentStage ? [currentStage] : []))
  const [query, setQuery] = useState('')
  // Navigating into another stage opens its group (never closes the ones the user opened).
  useEffect(() => { if (currentStage) setOpen(s => s.has(currentStage) ? s : new Set(s).add(currentStage)) }, [currentStage])
  const toggleStage = (id: string) => setOpen(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const go = (id: string) => { goScreen(id, true); setUi({ drawer: null }) }
  const lockOf = (id: string) => {
    const step = SUBTASKS.find(s => s.id === id)?.step
    const info = step ? snap?.steps[step - 1] : undefined
    return info?.state === 'locked' ? info.reason : undefined
  }
  // The search card is hidden on Upload, so a query typed elsewhere does not filter the stages there.
  const q = current === '1.1' && snap ? '' : query.trim().toLowerCase()
  const hits = q ? SUBTASKS.filter(s => !s.hidden && (s.label.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || stageOf(s.id)!.label.toLowerCase().includes(q))) : []
  const toggleRail = () => setUi(s => ({ shell: { ...s.shell, nav: !s.shell.nav } }))
  const onUpload = current === '1.1'
  // The stage the work is at: the first one not done (none once every stage is done).
  const active = TASKS.findIndex(t => taskStatus(t.id, statuses) !== 'done')

  if (collapsed) return (
    <nav className="shell-pane shell-rail" aria-label="Workflow navigation">
      <div className="rail-head">
        <button className="icon-btn sm" onClick={toggleRail} aria-expanded={false} aria-label="Expand navigation" title="Expand navigation"><Icon name="panel-right" size={14} /></button>
      </div>
      {/* Collapsed: each stage icon shows its name under it and in a tooltip (hover or focus), with the same marks as the open rail. */}
      <ul className="nv-mini">{TASKS.map((t, i) => {
        const st = taskStatus(t.id, statuses)
        return <li key={t.id}><Tip text={t.label} note={i === active ? `Current stage · ${STAGE_TEXT[st]}` : STAGE_TEXT[st]}>
          <button className={`nv-dot s-${st} ${currentStage === t.id ? 'on' : ''}`} onClick={() => { toggleRail(); setOpen(s => new Set(s).add(t.id)) }}
            aria-label={`${t.label}: ${STAGE_TEXT[st]}${i === active ? ', current stage' : ''}`}>
            <Icon name={st === 'done' ? 'check' : st === 'blocked' ? 'alert' : t.icon} size={15} />{i === active && st !== 'blocked' && <span className="nv-active" aria-hidden />}
          </button>
        </Tip><span className="nv-mini-label" aria-hidden>{t.label}</span></li>
      })}</ul>
    </nav>
  )

  return (
    <nav className="shell-pane navrail" aria-labelledby="nav-title">
      <header className="shell-head">
        <h2 id="nav-title">{onUpload ? 'Workflow' : <><Icon name="layout" size={13} />Navigate</>}</h2>
        <button className="icon-btn sm" onClick={toggleRail} aria-expanded aria-label="Collapse navigation" title="Collapse"><Icon name="panel-left" size={14} /></button>
      </header>
      <div className="nv-body">
        {/* Upload swaps the search card for + Add files in the same slot; the card stays laid out (invisible) so the slot
            keeps one height and the stages below never move between stages. */}
        <div className="nv-top" data-upload={onUpload && snap ? '' : undefined}>
        {onUpload && snap && <UploadFilesBlock snap={snap} />}
        <div className="nv-card">
          <strong className="nv-card-title">BAM pipeline</strong>
          <span className="muted xs">Track and manage your workflow</span>
          <label className="nv-search">
            <Icon name="search" size={13} />
            <span className="sr-only">Search workflow steps</span>
            <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search workflow steps"
              onKeyDown={e => { if (e.key === 'Escape') setQuery('') }} />
          </label>
        </div>
        </div>
        {q ? (
          <ul className="nv-items nv-results" aria-label="Search results">
            {hits.length ? hits.map(s => (
              <li key={s.id}><button className="nv-item" onClick={() => { setQuery(''); go(s.id) }}>
                <Icon name={s.icon} size={14} /><span className="nv-label">{s.label}</span><span className="muted xs">{stageOf(s.id)!.label}</span>
              </button></li>
            )) : <li className="muted xs nv-none">No step matches “{query}”.</li>}
          </ul>
        ) : (
          <ol className="nv-stages">
            {TASKS.map((t, i) => {
              const st = taskStatus(t.id, statuses)
              const isOpen = open.has(t.id)
                            const here = currentStage === t.id
              return (
                <li key={t.id} className={`nv-stage s-${st} ${here ? 'here' : ''}`}>
                  {i < TASKS.length - 1 && <span className="nv-line" aria-hidden />}
                  <button className="nv-stage-btn" aria-expanded={isOpen} aria-controls={`nv-group-${t.id}`} onClick={() => toggleStage(t.id)}>
                    {/* Check only when the stage is done; the current stage (first one not done) gets the active dot. */}
                    <span className="nv-mark" aria-hidden>{st === 'done' ? <Icon name="check" size={15} /> : st === 'blocked' ? <Icon name="alert" size={14} /> : <Icon name={t.icon} size={15} />}
                      {i === active && st !== 'blocked' && <span className="nv-active" />}</span>
                    <span className="nv-stage-text">
                      <span className="nv-stage-name">{t.label}{i === active && <span className="sr-only">, current stage</span>}</span>
                      <span className="nv-stage-status">{STAGE_TEXT[st]}</span>
                    </span>
                  </button>
                  <ul id={`nv-group-${t.id}`} className="nv-items" hidden={!isOpen}>
                    {subtasksOf(t.id).map(s => {
                      const lock = lockOf(s.id)
                      const sst = statuses[s.id] ?? 'not_started'
                      // Workbench and Validation are human-gated: every step shows its state (never looks skipped).
                      const gated = s.parentId === '3' || s.parentId === '4'
                      return (
                        <li key={s.id} className={s.mode ? 'nv-auto-row' : undefined}>
                          <button className={`nv-item ${current === s.id ? 'on' : ''}`} aria-current={current === s.id ? 'page' : undefined} onClick={() => go(s.id)}
                            title={lock ? `Locked: ${lock}` : s.description} aria-describedby={lock ? `nv-lock-${s.id}` : undefined}>
                            <Icon name={s.icon} size={16} />
                            <span className="nv-label">{s.label}</span>{s.optional && <span className="nv-opt">optional</span>}{s.mode && <span className="nv-auto-slot" aria-hidden />}
                            {sst === 'done' ? <span className="nv-done" aria-hidden><Icon name="check" size={11} /></span>
                              : !lock && gated ? <StepMark st={sst} /> : null}
                            <span className="sr-only">{lock ? '' : `, ${STATUS_LABEL[sst]}`}</span>
                          </button>
                          {/* The chip is focusable, so it sits beside the button (never inside it), over the slot left of the check. */}
                          {s.mode === 'automated' && <AutoChip className={sst === 'done' ? 'nv-auto has-mark' : 'nv-auto'} />}
                          {s.mode === 'hitl' && <HitlChip className={sst === 'done' ? 'nv-auto has-mark' : 'nv-auto'} />}
                          {lock && <span id={`nv-lock-${s.id}`} className="sr-only">Locked: {lock}</span>}
                        </li>
                      )
                    })}
                  </ul>
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </nav>
  )
}
