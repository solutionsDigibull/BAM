export const DEFAULT_PANE_ORDER = ['nav', 'assistant', 'workspace'] as const
export type PaneId = typeof DEFAULT_PANE_ORDER[number]
export type PaneOrder = [PaneId, PaneId, PaneId]
export function paneOrder(value: unknown): PaneOrder {
  return Array.isArray(value) && value.length === 3 && new Set(value).size === 3 && value.every(p => DEFAULT_PANE_ORDER.includes(p))
    ? [...value] as PaneOrder : [...DEFAULT_PANE_ORDER]
}
export function swapPanes(order: PaneOrder, source: PaneId, target: PaneId): PaneOrder {
  const next = [...order] as PaneOrder
  const a = next.indexOf(source), b = next.indexOf(target)
  ;[next[a], next[b]] = [next[b]!, next[a]!]
  return next
}
export const paneOrderKey = (user: string) => `bam-pane-order-v1:${user.trim().toLowerCase()}`

/* ---- Pane widths (17 Nov 2026, approved wireframes): NAVIGATE 20% · assistant 40% · RESULTS & EDITS 40% ----
   The workspace is always the remainder, so only nav and the assistant are stored.
   Percentages are of the shell's content width (between the gaps), as whole numbers. */
export interface PaneWidths { nav: number; assist: number }
export const DEFAULT_PANE_WIDTHS: PaneWidths = { nav: 20, assist: 40 }
/** No pane ever goes below this share, so a drag can never lose a pane. */
export const MIN_PANE_PCT = 10
export const paneWidthsKey = (user: string) => `bam-pane-widths-v1:${user.trim().toLowerCase()}`
export const workspacePct = (w: PaneWidths) => 100 - w.nav - w.assist

export function clampWidths(w: PaneWidths): PaneWidths {
  let nav = Math.round(w.nav), assist = Math.round(w.assist)
  nav = Math.min(Math.max(nav, MIN_PANE_PCT), 100 - 2 * MIN_PANE_PCT)
  assist = Math.min(Math.max(assist, MIN_PANE_PCT), 100 - MIN_PANE_PCT - nav)
  return { nav, assist }
}

export function paneWidths(value: unknown): PaneWidths {
  const w = value as Partial<Record<keyof PaneWidths, unknown>> | null
  return typeof w?.nav === 'number' && typeof w?.assist === 'number' ? clampWidths({ nav: w.nav, assist: w.assist }) : { ...DEFAULT_PANE_WIDTHS }
}

/** Dragging the divider between two adjacent panes by `delta` percentage points (positive grows the left pane).
 *  The workspace is the remainder, so it compensates for every adjustment. */
export function dragWidths(w: PaneWidths, left: PaneId, right: PaneId, delta: number): PaneWidths {
  const pct: Record<PaneId, number> = { nav: w.nav, assistant: w.assist, workspace: 100 - w.nav - w.assist }
  const lo = MIN_PANE_PCT, hi = 100 - 2 * MIN_PANE_PCT
  const d = Math.min(Math.max(delta, lo - pct[left], pct[right] - hi), pct[right] - lo, hi - pct[left])
  pct[left] += d
  pct[right] -= d
  return clampWidths({ nav: pct.nav, assist: pct.assistant })
}

/** First-use swap hint (approved wireframes, change 3): shown until "Got it" or the first successful swap. */
export const PANE_HINT_KEY = 'bam-pane-hint-v1'
