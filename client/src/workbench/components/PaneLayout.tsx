import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type HTMLAttributes, type PointerEvent } from 'react'
import { flushSync } from 'react-dom'
import { useSession } from '../features/login/session'
import { useUi } from '../lib/store'
import {
  DEFAULT_PANE_ORDER, DEFAULT_PANE_WIDTHS, PANE_HINT_KEY, dragWidths,
  paneOrder, paneOrderKey, paneWidths, paneWidthsKey, swapPanes, workspacePct,
  type PaneId, type PaneOrder, type PaneWidths,
} from '../lib/paneLayout'
import { Icon } from './ui'

export const paneHeaderProps = {
  tabIndex: 0,
  title: 'Drag this header to swap panes. Or press Alt + Left/Right.',
  'data-pane-handle': '',
} as const
const paneAt = (el: Element | null) => el?.closest<HTMLElement>('[data-layout-pane]')?.dataset.layoutPane as PaneId | undefined
const canArrange = () => matchMedia('(min-width: 861px)').matches && !['1', '2'].includes(document.querySelector<HTMLElement>('.bam-wb')?.dataset.uploadStage ?? '')
const readOrder = (key: string): PaneOrder => {
  try { return paneOrder(JSON.parse(localStorage.getItem(key) ?? 'null')) } catch { return paneOrder(null) }
}
const readWidths = (key: string): PaneWidths => {
  try { return paneWidths(JSON.parse(localStorage.getItem(key) ?? 'null')) } catch { return paneWidths(null) }
}
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches

/** Where a pane sits in the current visual order (0 = left edge, 2 = right edge). */
const PaneOrderCtx = createContext<{ order: PaneOrder; moveLeft: (p: PaneId) => void }>({ order: [...DEFAULT_PANE_ORDER], moveLeft: () => {} })
export const usePaneSpot = (pane: PaneId) => useContext(PaneOrderCtx).order.indexOf(pane)

/** Swap grip in every pane header (17 Nov 2026, per user - replaces the six-dot grip from the approved wireframes,
 *  change 3): the tabler "replace" icon she supplied verbatim (17 Nov 2026), highlighted on a chip at the
 *  header's left-edge slot, same in all three panes. It is also
 *  an action button: one click swaps the pane one position left (disabled at the left edge); header drag and
 *  Alt+Arrow still work. */
export function PaneGrip({ pane }: { pane: PaneId }) {
  const { moveLeft } = useContext(PaneOrderCtx)
  return (
    <button type="button" className="pane-grip" onClick={() => moveLeft(pane)}
      aria-label="Move pane one position left" title="Move pane left">
      {/* 17 Nov 2026, per user: the exact tabler "replace" icon she supplied (MIT, no attribution needed). */}
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path stroke="none" d="M0 0h24v24H0z" fill="none" />
        <path d="M3 4a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4" />
        <path d="M15 16a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1l0 -4" />
        <path d="M21 11v-3a2 2 0 0 0 -2 -2h-6l3 3m0 -6l-3 3" />
        <path d="M3 13v3a2 2 0 0 0 2 2h6l-3 -3m0 6l3 -3" />
      </svg>
    </button>
  )
}

/** Collapsed pane (approved wireframes, change 4): a 6 px hairline in the background colour - no icon, no label.
 *  Hovering the edge fades in a slim strip with a chevron; click or pull it to restore the pane. */
export function Hairline({ pane, label, onRestore }: { pane: 'nav' | 'assistant'; label: string; onRestore: () => void }) {
  const spot = usePaneSpot(pane)
  const opens = spot === 2 ? 'left' : spot === 0 ? 'right' : pane === 'nav' ? 'right' : 'left'
  const pull = useRef<number | null>(null)
  return (
    <div className="shell-pane shell-hair" data-opens={opens}
      onPointerDown={e => {
        // A press on the restore strip stays a plain click; a press on the hairline itself can pull to restore.
        if (e.button !== 0 || (e.target as HTMLElement).closest('.hair-restore')) return
        pull.current = e.clientX
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={e => {
        const x = pull.current
        if (x == null) return
        const d = e.clientX - x
        if ((opens === 'right' && d > 36) || (opens === 'left' && d < -36)) { pull.current = null; onRestore() }
      }}
      onPointerUp={() => { pull.current = null }}
      onPointerCancel={() => { pull.current = null }}>
      <span className="hair-hot" aria-hidden />
      <button type="button" className="hair-restore" onClick={onRestore} aria-expanded={false} aria-label={label} title={label}>
        <Icon name="chevron" size={14} />
      </button>
    </div>
  )
}

/** CSS ordering keeps the actual pane nodes mounted, including chat, editors, drafts and scroll positions. */
export function PaneLayout({ children, ...props }: HTMLAttributes<HTMLDivElement>) {
  const user = useSession() ?? ''
  const key = paneOrderKey(user)
  const wKey = paneWidthsKey(user)
  const shell = useUi(s => s.shell)
  const [saved, setSaved] = useState(() => ({ key, order: readOrder(key) }))
  const order = saved.key === key ? saved.order : readOrder(key)
  const [savedW, setSavedW] = useState(() => ({ key: wKey, widths: readWidths(wKey) }))
  const widths = savedW.key === wKey ? savedW.widths : readWidths(wKey)
  const root = useRef<HTMLDivElement>(null)
  const gesture = useRef<{
    source: PaneId; x: number; y: number; lastX: number; lastY: number
    moving: boolean; home: PaneOrder; ghost?: HTMLDivElement; homeRect?: DOMRect
  } | null>(null)
  const [drag, setDrag] = useState<PaneId | null>(null)
  const rszGesture = useRef<{ left: PaneId; right: PaneId; startX: number; start: PaneWidths; contentW: number } | null>(null)
  const [rsz, setRsz] = useState<{ i: number; x: number; y: number } | null>(null)
  const [hint, setHint] = useState<{ x: number; y: number } | null>(null)

  const orderRef = useRef(order)
  orderRef.current = order
  const persistOrder = (next: PaneOrder) => {
    orderRef.current = next
    setSaved({ key, order: next })
    try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* Layout still works with storage blocked. */ }
  }
  const persistWidths = (next: PaneWidths) => {
    setSavedW({ key: wKey, widths: next })
    try { localStorage.setItem(wKey, JSON.stringify(next)) } catch { /* storage blocked */ }
  }
  const pctOf = (p: PaneId) => p === 'nav' ? widths.nav : p === 'assistant' ? widths.assist : workspacePct(widths)
  const collapsed = (p: PaneId) => p === 'nav' ? shell.nav : p === 'assistant' ? shell.assistant : false
  // Collapsed widths (17 Nov 2026, per user): NAVIGATE keeps a slim 56px icon rail (its stage icons); the
  // assistant folds to the 6px hairline from the approved wireframes.
  const collapsedPx = (p: PaneId) => p === 'nav' ? 56 : 6
  const hintDone = () => {
    try { localStorage.setItem(PANE_HINT_KEY, 'done') } catch { /* storage blocked */ }
    setHint(null)
  }

  /* Esc cancels an in-flight drag (change 2). Focus is rarely inside the shell mid-drag, so this listens
     at the window; the shell's own onKeyDown still covers a focused handle. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const g = gesture.current
      if (!g) return
      gesture.current = null
      setDrag(null)
      if (g.moving) settle(g, true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* Re-opening a collapsed pane returns every pane to the default 20 / 40 / 40 (approved wireframes, change 4). */
  const prevShell = useRef(shell)
  useEffect(() => {
    const prev = prevShell.current
    prevShell.current = shell
    if ((prev.nav && !shell.nav) || (prev.assistant && !shell.assistant)) persistWidths({ ...DEFAULT_PANE_WIDTHS })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shell])

  /* First-use hint (change 3): one-time bubble pointing at a grip. It retires forever after "Got it" or the
     first successful swap; "Show me again later" only closes it for this visit. */
  useEffect(() => {
    let shown = false
    const attempt = () => {
      if (shown) return
      try { if (localStorage.getItem(PANE_HINT_KEY) === 'done') { shown = true; return } } catch { /* show */ }
      if (!canArrange()) return
      const grip = root.current?.querySelector('[data-layout-pane="assistant"] .pane-grip') ?? root.current?.querySelector('.pane-grip')
      const r = grip?.getBoundingClientRect()
      if (!r || r.width === 0) return
      shown = true
      setHint({ x: r.left + r.width / 2, y: r.bottom + 10 })
    }
    const t = window.setInterval(attempt, 1200)
    attempt()
    return () => window.clearInterval(t)
  }, [])

  /** Pixel width of a pane as the committed grid lays it out: 6px hairline when collapsed; on Upload the
   *  fr shares (goal.css: nav .8fr · assistant 1.6fr · workspace 1fr, split among the open panes); elsewhere
   *  the 20/40/40 shares. The last column takes whatever is left. */
  const pxWidthOf = (p: PaneId, cur: PaneOrder, contentW: number, gap: number): number => {
    if (collapsed(p)) return collapsedPx(p)
    const total = contentW - 2 * gap
    if (onUpload) {
      const frac: Record<PaneId, number> = { nav: 0.8, assistant: 1.6, workspace: 1 }
      const openSum = cur.reduce((s, q) => s + (collapsed(q) ? 0 : frac[q]), 0)
      const hairlines = cur.reduce((s, q) => s + (collapsed(q) ? collapsedPx(q) : 0), 0)
      return frac[p] / openSum * (total - hairlines)
    }
    return total * pctOf(p) / 100
  }

  /** Which visual column a client x lands in, from the committed layout (never the mid-glide positions),
   *  so a live reorder cannot oscillate while panes are still gliding (change 2). */
  const columnAt = (clientX: number): number => {
    const el = root.current
    if (!el) return 0
    const cs = getComputedStyle(el)
    const gap = parseFloat(cs.columnGap) || 12
    const padL = parseFloat(cs.paddingLeft) || 12
    const padR = parseFloat(cs.paddingRight) || 12
    const contentW = el.clientWidth - padL - padR
    const cur = orderRef.current
    const lastOpen = cur.reduce((last, p, i) => (collapsed(p) ? last : i), -1)
    let x = el.getBoundingClientRect().left + padL
    for (let i = 0; i < cur.length; i++) {
      const w = i === lastOpen
        ? contentW - 2 * gap - cur.slice(0, i).reduce((s, q) => s + pxWidthOf(q, cur, contentW, gap), 0)
        : pxWidthOf(cur[i]!, cur, contentW, gap)
      if (clientX < x + w + gap / 2) return i
      x += w + gap
    }
    return cur.length - 1
  }

  /** Neighbours glide aside in real time (change 2): FLIP-animate the slots around an order change. */
  const glide = (mutate: () => void) => {
    const el = root.current
    if (!el || reducedMotion()) { mutate(); return }
    const slots = Array.from(el.querySelectorAll<HTMLElement>('[data-layout-pane]'))
    const before = new Map(slots.map(s => [s, s.getBoundingClientRect()]))
    flushSync(mutate)
    for (const s of slots) {
      const b = before.get(s)!
      const a = s.getBoundingClientRect()
      const dx = b.left - a.left, dy = b.top - a.top
      if (dx || dy) s.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 220, easing: 'cubic-bezier(.2,.6,.2,1)' })
    }
  }

  const startGhost = (g: NonNullable<typeof gesture.current>) => {
    const slot = root.current?.querySelector<HTMLElement>(`[data-layout-pane="${g.source}"]`)
    if (!slot) return
    const rect = slot.getBoundingClientRect()
    const ghost = document.createElement('div')
    ghost.className = 'pane-ghost'
    ghost.setAttribute('aria-hidden', 'true')
    ghost.appendChild(slot.cloneNode(true))
    Object.assign(ghost.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` })
    // Inside the workbench root so the .bam-wb scoped styles and tokens still apply to the ghost.
    ;(root.current?.closest('.bam-wb') ?? document.body).appendChild(ghost)
    slot.dataset.dropSlot = ''
    g.ghost = ghost
    g.homeRect = rect
  }

  const cleanupGhost = (g: NonNullable<typeof gesture.current>) => {
    g.ghost?.remove()
    root.current?.querySelector(`[data-layout-pane="${g.source}"]`)?.removeAttribute('data-drop-slot')
    g.ghost = undefined
  }

  /** Drop, Esc or pointer cancel: the ghost slides into its slot and settles in ~220 ms (ease-out);
   *  cancelling glides the order back home first. */
  const settle = (g: NonNullable<typeof gesture.current>, cancel: boolean) => {
    const ghost = g.ghost
    const slot = root.current?.querySelector<HTMLElement>(`[data-layout-pane="${g.source}"]`)
    if (!ghost) { if (slot) slot.removeAttribute('data-drop-slot'); return }
    const changed = orderRef.current.join() !== g.home.join()
    if (cancel && changed) glide(() => persistOrder(g.home))
    if (!cancel && changed) hintDone()
    const rect = slot?.getBoundingClientRect()
    const home = g.homeRect!
    const fromX = g.lastX - g.x, fromY = g.lastY - g.y
    const toX = (rect?.left ?? home.left) - home.left, toY = (rect?.top ?? home.top) - home.top
    if (reducedMotion()) { cleanupGhost(g); return }
    ghost.style.transform = `translate(${fromX}px, ${fromY}px)`
    const anim = ghost.animate(
      [{ transform: `translate(${fromX}px, ${fromY}px)` }, { transform: `translate(${toX}px, ${toY}px)` }],
      { duration: 220, easing: 'cubic-bezier(.2,.6,.2,1)' })
    anim.finished.then(() => cleanupGhost(g), () => cleanupGhost(g))
  }

  /* Column widths: the last pane in the visual order is the flexible one, so a collapsed pane's space always
     goes to the right-hand survivor (approved wireframes, changes 4 and 5). Upload (screen 1.1) keeps its own
     earlier widths (goal.css), so the 20/40/40 shares, hairline widths and resize dividers don't apply there. */
  const onUpload = (props as Record<string, unknown>)['data-screen'] === '1.1'
  // The last OPEN pane in the visual order is the flexible one (a collapsed pane is always a 6px hairline).
  const lastOpen = order.reduce((last, p, i) => (collapsed(p) ? last : i), -1)
  const colExpr = (p: PaneId, i: number): string => {
    if (collapsed(p)) return `${collapsedPx(p)}px`
    if (i === lastOpen) return 'minmax(0, 1fr)'
    return `calc((100% - 2 * var(--gap)) * ${pctOf(p) / 100})`
  }
  const legacyWidth: Record<PaneId, string> = { nav: 'var(--nav)', assistant: 'var(--assist)', workspace: 'minmax(0, 1fr)' }
  const columns = onUpload
    ? order.map(p => collapsed(p) ? `${collapsedPx(p)}px` : legacyWidth[p])
    : order.map((p, i) => colExpr(p, i))
  const dividerLeft = (i: number) =>
    `calc(var(--gap) + ${columns.slice(0, i + 1).join(' + ')} + ${i} * var(--gap) + var(--gap) / 2)`
  const dividers = onUpload ? [] : [0, 1]
    .map(i => ({ i, left: order[i]!, right: order[i + 1]! }))
    .filter(d => !collapsed(d.left) && !collapsed(d.right))

  const style = {
    ...props.style,
    '--pane-columns': columns.join(' '),
    '--order-nav': order.indexOf('nav'),
    '--order-assistant': order.indexOf('assistant'),
    '--order-workspace': order.indexOf('workspace'),
  } as CSSProperties

  /** One click on the grip button swaps the pane one position left (17 Nov 2026, per user); same glide as a drag. */
  const moveLeft = (p: PaneId) => {
    const cur = orderRef.current
    const i = cur.indexOf(p)
    if (i < 0) return
    // 17 Nov 2026, per user: clicking must always move the pane - at the left edge it wraps to the far right.
    const next = i === 0 ? ([...cur.slice(1), p] as PaneOrder) : swapPanes(cur, p, cur[i - 1]!)
    glide(() => persistOrder(next))
    hintDone()
  }

  return <PaneOrderCtx.Provider value={{ order, moveLeft }}>
    <div {...props} ref={root} style={style} data-pane-order={order.join(',')} data-pane-drag={drag ?? undefined}
    onPointerDownCapture={e => {
      const el = e.target as HTMLElement
      if (!canArrange() || e.button !== 0 || el.closest('button, a, input, textarea, select, [contenteditable="true"], .pane-divider') || !el.closest('[data-pane-handle]')) return
      const source = paneAt(el)
      if (!source) return
      gesture.current = { source, x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY, moving: false, home: order }
      e.currentTarget.setPointerCapture(e.pointerId)
    }}
    onPointerMove={e => {
      const g = gesture.current
      if (!g) return
      if (!g.moving && Math.hypot(e.clientX - g.x, e.clientY - g.y) < 6) return
      if (!g.moving) { g.moving = true; startGhost(g); setDrag(g.source) }
      g.lastX = e.clientX; g.lastY = e.clientY
      e.preventDefault()
      if (g.ghost) g.ghost.style.transform = `translate(${e.clientX - g.x}px, ${e.clientY - g.y}px)`
      // Live reorder: the dragged pane takes the column the pointer is in; neighbours glide aside.
      const cur = orderRef.current
      const from = cur.indexOf(g.source)
      const to = columnAt(e.clientX)
      if (to !== from) {
        const next = cur.filter(p => p !== g.source) as PaneId[]
        next.splice(to, 0, g.source)
        glide(() => persistOrder(next as PaneOrder))
      }
    }}
    onPointerUp={e => {
      const g = gesture.current
      gesture.current = null
      setDrag(null)
      if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
      if (g?.moving) { g.lastX = e.clientX; g.lastY = e.clientY; settle(g, false) }
    }}
    onPointerCancel={e => {
      const g = gesture.current
      gesture.current = null
      setDrag(null)
      if (g?.moving) settle(g, true)
    }}
    onLostPointerCapture={() => {
      const g = gesture.current
      gesture.current = null
      setDrag(null)
      if (g?.moving) settle(g, true)
    }}
    onKeyDown={e => {
      if (e.key === 'Escape') {
        const g = gesture.current
        gesture.current = null
        setDrag(null)
        if (g?.moving) settle(g, true)
        return
      }
      if (!canArrange() || !e.altKey || !['ArrowLeft', 'ArrowRight'].includes(e.key) || !(e.target as HTMLElement).matches('[data-pane-handle]')) return
      const source = paneAt(e.target as HTMLElement)
      if (!source) return
      const target = order[order.indexOf(source) + (e.key === 'ArrowLeft' ? -1 : 1)]
      if (target) { e.preventDefault(); glide(() => persistOrder(swapPanes(order, source, target))); hintDone() }
    }}>
    {children}
    {dividers.map(d => (
      <div key={`${d.left}|${d.right}`}
        className={`pane-divider${rsz?.i === d.i ? ' on' : ''}`}
        style={{ left: dividerLeft(d.i) }}
        // 17 Nov 2026, per user: the grip pill appears where the cursor points along the line, not only mid-line.
        onPointerEnter={e => { const r = e.currentTarget.getBoundingClientRect(); e.currentTarget.style.setProperty('--grip-y', `${Math.min(Math.max(e.clientY - r.top, 16), r.height - 16)}px`) }}
        onPointerMoveCapture={e => { if (rszGesture.current) return; const r = e.currentTarget.getBoundingClientRect(); e.currentTarget.style.setProperty('--grip-y', `${Math.min(Math.max(e.clientY - r.top, 16), r.height - 16)}px`) }}
        role="separator" aria-orientation="vertical" tabIndex={0}
        aria-label={`Resize ${d.left} and ${d.right} panes`}
        title="Drag to resize · double-click to reset"
        onPointerDown={e => {
          if (e.button !== 0) return
          const el = root.current
          if (!el) return
          const cs = getComputedStyle(el)
          const gap = parseFloat(cs.columnGap) || 12
          const contentW = el.clientWidth - (parseFloat(cs.paddingLeft) || 12) - (parseFloat(cs.paddingRight) || 12) - 2 * gap
          rszGesture.current = { left: d.left, right: d.right, startX: e.clientX, start: widths, contentW }
          e.currentTarget.setPointerCapture(e.pointerId)
          // No preventDefault here: cancelling pointerdown would swallow the double-click reset.
          setRsz({ i: d.i, x: e.clientX, y: e.clientY })
        }}
        onPointerMove={e => {
          const g = rszGesture.current
          if (!g) return
          e.preventDefault() // block text selection only once a resize is actually moving
          setSavedW({ key: wKey, widths: dragWidths(g.start, g.left, g.right, (e.clientX - g.startX) / g.contentW * 100) })
          setRsz({ i: d.i, x: e.clientX, y: e.clientY })
        }}
        onPointerUp={e => {
          const g = rszGesture.current
          rszGesture.current = null
          setRsz(null)
          if (g) persistWidths(dragWidths(g.start, g.left, g.right, (e.clientX - g.startX) / g.contentW * 100))
        }}
        onPointerCancel={() => { rszGesture.current = null; setRsz(null) }}
        onDoubleClick={() => persistWidths({ ...DEFAULT_PANE_WIDTHS })}
        onKeyDown={e => {
          if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return
          e.preventDefault()
          persistWidths(dragWidths(widths, d.left, d.right, e.key === 'ArrowRight' ? 1 : -1))
        }} />
    ))}
    {rsz && (
      <div className="pane-readout" style={{ left: rsz.x, top: rsz.y + 18 }} aria-hidden>
        {order.map(p => `${pctOf(p)}%`).join(' · ')}
      </div>
    )}
    {hint && (
      <div className="pane-hint" style={{ left: hint.x, top: hint.y }} role="dialog" aria-label="Panes can be rearranged">
        <p>Drag a pane by its header, or click its swap icon, to rearrange your workspace.</p>
        <div className="pane-hint-actions">
          <button type="button" className="pane-hint-ok" onClick={hintDone}>Got it</button>
          <button type="button" className="pane-hint-later" onClick={() => setHint(null)}>Show me again later</button>
        </div>
      </div>
    )}
  </div>
  </PaneOrderCtx.Provider>
}
