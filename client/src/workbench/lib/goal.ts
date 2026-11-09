// The FDE's goal for a project ("Build an AI agent that automates ... for ... in ..."), typed on the Upload screen.
// It is kept in this browser only, one goal per project: it is not sent to the backend and does not feed the SOW. Whether
// it should be stored on the project is decision #720. Nothing is generated from it: there is no invented draft.
import { useSyncExternalStore } from 'react'

export interface Goal { what: string; who: string; where: string }
/** `saved` opens "+ Add files". `text` is empty for a project that already had work when this screen was introduced. */
export interface GoalState { goal: Goal; text: string; saved: boolean }

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)
export const sentence = (g: Goal) => `Build an AI agent that automates ${lowerFirst(g.what)} for ${lowerFirst(g.who)} in ${g.where.trim()}`

/** Splits a typed goal "<what> for <who> in <where>" into three parts. Free-form text is valid: parts that are not
 *  written fall back to neutral wording, and the whole text stays the What. */
export function parseGoal(text: string): Goal {
  const t = text.trim().replace(/^build an ai agent that automates\s+/i, '').replace(/[.\s]+$/, '')
  const m = /^(.*?)\s+for\s+(.*?)(?:\s+in\s+(.+))?$/i.exec(t)
  if (m && m[1]!.trim() && m[2]!.trim()) return { what: m[1]!.trim(), who: m[2]!.trim(), where: (m[3] ?? 'your market').trim() }
  return { what: t, who: 'your team', where: 'your market' }
}

// ---- the store (one goal per project, saved in this browser as `bam-goal-v3:<project>`) -------------------------
const EMPTY: GoalState = { goal: { what: '', who: '', where: '' }, text: '', saved: false }
const KEY = (project: string) => `bam-goal-v3:${project}`
/** The project whose workbench is open (its id is in the URL); one module serves every project in a session. */
const projectId = () => (typeof window === 'undefined' ? undefined : /\/projects\/([^/]+)/.exec(window.location.pathname)?.[1]) ?? 'default'

let loadedFor = ''
let gs: GoalState = EMPTY
function sync() {
  const project = projectId()
  if (project === loadedFor) return
  loadedFor = project
  gs = EMPTY
  try {
    localStorage.removeItem('bam-goal-v2') // the earlier, template-based goal: dropped
    const s = JSON.parse(localStorage.getItem(KEY(project)) ?? 'null') as GoalState | null
    if (s?.saved) gs = { goal: s.goal ?? EMPTY.goal, text: s.text ?? '', saved: true }
  } catch { /* none, unreadable or blocked: start without a goal */ }
}
const subs = new Set<() => void>()
function commit(next: GoalState) {
  sync()
  gs = next
  try { localStorage.setItem(KEY(loadedFor), JSON.stringify(gs)) } catch { /* storage blocked: the goal lasts for this session */ }
  subs.forEach(f => f())
}
export const getGoal = (): GoalState => { sync(); return gs }
export const useGoal = <T,>(select: (s: GoalState) => T) =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => select(getGoal()), () => select(EMPTY))

/** Saves the goal as typed. */
export function saveGoal(raw: string) {
  const text = raw.trim()
  if (text) commit({ goal: parseGoal(text), text, saved: true })
}
/** A project that already has uploads or a pipeline run resumes where the work is: it does not ask for a goal first. */
export function skipGoal() { if (!getGoal().saved) commit({ goal: EMPTY.goal, text: '', saved: true }) }
export const resetGoal = () => commit(EMPTY)

/** "+ Add files" opens as soon as a goal is saved (no approval step). */
export const canAddFiles = (s: GoalState) => s.saved
/** The one lock every upload path checks (Add files menu, picker, drops, the review dialog's Add more, the uploader). */
export const addFilesLocked = () => !canAddFiles(getGoal())

/** Pane progression on Upload: 1 = the goal box alone, 2 = goal saved (files pane opens), 3 = files added (outputs pane opens). */
export const goalStage = (s: GoalState, files: number): 1 | 2 | 3 => !s.saved ? 1 : files > 0 ? 3 : 2
