import { useSyncExternalStore } from 'react'
import type { Snapshot, StepId } from '../api/types'
import { ROUTE_ALIASES, SCREEN_ROUTES, STEP_SCREEN } from '../tracker/trackerConfig.ts'

// ponytail: History API instead of a router library; swap for Refine's router binding if routes grow.
const subs = new Set<() => void>()
const emit = () => subs.forEach(f => f())
// Browsed = the person picked this screen (left rail, back/forward). Automated steps reached by the flow, a reload or
// a redirect move on as soon as their work is done; a browsed one stays put so it can be looked at (no bounce).
let browsed = false
export const browsedHere = () => browsed
if (typeof window !== 'undefined') window.addEventListener('popstate', () => { browsed = true; emit() })

export function navigate(path: string, { replace = false, browse = false } = {}) {
  if (location.pathname === path) return
  browsed = browse
  if (replace) history.replaceState(null, '', path)
  else history.pushState(null, '', path)
  emit()
}

export const usePath = () =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => location.pathname)

/** The current path for an old one (v3 moved or split some screens), or undefined. */
export const aliasOf = (path: string): string | undefined => ROUTE_ALIASES[path.replace(/\/+$/, '') || '/']

export const RAG_ROUTES = { graph: SCREEN_ROUTES['2.1'].path, schemas: SCREEN_ROUTES['2.2'].path, quality: SCREEN_ROUTES['2.3'].path, review: SCREEN_ROUTES['2.4'].path } as const

export const goScreen = (id: string, browse = false) => { const r = SCREEN_ROUTES[id]; if (r) navigate(r.path, { browse }) }
/** Open the screen where a backend workbench step is worked on. */
export const goStep = (step: StepId) => goScreen(STEP_SCREEN[step])

/** Where the work is right now: Upload first, then Standardize until the batch is ingested, then Parse, then the active workbench step. */
export function activeScreen(snap: Snapshot): string {
  if (snap.activeStep === 1 && !snap.sow) return !snap.uploads.length ? '1.1' : !snap.ingestion ? '1.8' : '1.9'
  return STEP_SCREEN[snap.activeStep]
}
