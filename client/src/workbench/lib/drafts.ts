// Unsaved editor drafts for 4.3 Generated code and 4.2 Manifest JSON. One store shared by every screen
// (and 3.3), so moving between routes never loses an edit and never forks a second copy of the state.
import { useSyncExternalStore } from 'react'

export interface Drafts {
  /** Unsaved code by file path. */
  code: Record<string, string>
  /** Unsaved manifest JSON. */
  manifest?: string
}

let state: Drafts = { code: {} }
const subs = new Set<() => void>()

export function setDrafts(patch: Partial<Drafts> | ((d: Drafts) => Partial<Drafts>)) {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
  subs.forEach(f => f())
}
export const getDrafts = () => state
export const useDrafts = <T,>(select: (d: Drafts) => T) =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => select(state))

export const setCodeDraft = (path: string, value: string | undefined) => setDrafts(d => {
  const code = { ...d.code }
  if (value === undefined) delete code[path]; else code[path] = value
  return { code }
})

/** Drafts that differ from what the server holds. */
export function unsaved(d: Drafts, saved: { code: Record<string, string>; manifest?: string }) {
  const files = Object.keys(d.code).filter(p => saved.code[p] !== undefined && d.code[p] !== saved.code[p])
  const manifest = d.manifest !== undefined && saved.manifest !== undefined && d.manifest !== saved.manifest
  return { files, manifest, any: files.length > 0 || manifest }
}
