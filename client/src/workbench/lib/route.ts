import { useSyncExternalStore } from 'react'
import type { Snapshot, StepId } from '../api/types'
import { ROUTE_ALIASES, SCREEN_ROUTES, STEP_SCREEN } from '../tracker/trackerConfig'

// The workbench lives on one Next route (/projects/[projectId]); its screens are the URL hash (#/ingestion/upload).
// A hash change never starts a Next navigation, and deep links, reload and Back keep working.
const subs = new Set<() => void>()
const emit = () => subs.forEach(f => f())
// Browsed = the person picked this screen (left rail, back/forward). Automated steps reached by the flow, a reload or
// a redirect move on as soon as their work is done; a browsed one stays put so it can be looked at (no bounce).
let browsed = false
export const browsedHere = () => browsed
const read = (): string => (typeof location === 'undefined' ? '/' : location.hash.replace(/^#/, '') || '/')
if (typeof window !== 'undefined') window.addEventListener('popstate', () => { browsed = true; emit() })

export function navigate(path: string, { replace = false, browse = false } = {}) {
  if (read() === path) return
  browsed = browse
  const url = `${location.pathname}${location.search}#${path}`
  if (replace) history.replaceState(null, '', url)
  else history.pushState(null, '', url)
  emit()
}

export const usePath = () =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, read, () => '/')

/** The current path for an old one (v3 moved or split some screens), or undefined. */
export const aliasOf = (path: string): string | undefined => ROUTE_ALIASES[path.replace(/\/+$/, '') || '/']

const pathOf = (id: string): string => SCREEN_ROUTES[id]?.path ?? '/'
export const RAG_ROUTES = { graph: pathOf('2.1'), schemas: pathOf('2.2'), quality: pathOf('2.3'), review: pathOf('2.4') } as const

export const goScreen = (id: string, browse = false) => { const r = SCREEN_ROUTES[id]; if (r) navigate(r.path, { browse }) }
/** Open the screen where a backend workbench step is worked on. */
export const goStep = (step: StepId) => goScreen(STEP_SCREEN[step])

/** Where the work is right now: Upload first, then Parse once the batch is ingested, then the active workbench step. */
export function activeScreen(snap: Snapshot): string {
  if (snap.activeStep === 1 && !snap.sow) return !snap.uploads.length || !snap.ingestion ? '1.1' : '1.9'
  return STEP_SCREEN[snap.activeStep]
}
