// The FDE's goal for a project ("Build an AI agent that automates ... for ... in ..."), typed on the goal screen (#/goal).
// It is stored on the project (#720: GET / PUT /projects/{id}/goal with If-Match), so every FDE and device sees the same
// goal, and the knowledge build and SOW generation read it as extra context. Nothing is generated from it here.
// With it the API sends the fixed ISATVON template shown under the goal (#798, read-only): it is kept as sent.
import { useSyncExternalStore } from 'react'

import type { GoalIsatvonSection, ProjectGoalOut } from '@/contracts'

import { ApiFailure, getJson, postJson, putJson } from '../http/http'

export interface Goal { what: string; who: string; where: string }
/** `saved` opens "+ Add files". `loaded` is false until the project's goal has been read (no redirect before that).
 *  `text` is empty for a project that already had work when this screen was introduced. `error` says why the last
 *  save did not reach the project (the goal is kept on screen, so the work can go on). `isatvon` is the template the
 *  server sent with the goal (#798): empty until the project has a saved goal. */
export interface GoalState { goal: Goal; text: string; saved: boolean; loaded: boolean; isatvon: GoalIsatvonSection[]; error?: string }

/** The server's limit (`sbam_contracts.projects.GOAL_MAX`). */
export const GOAL_MAX = 300

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
/** One line, as the server stores it: whitespace (newlines included) collapsed, trimmed. */
export const cleanGoal = (raw: string) => raw.replace(/\s+/g, ' ').trim()

// ---- the store: one goal per project, read from and written to the project ------------------------------------
const EMPTY: GoalState = { goal: { what: '', who: '', where: '' }, text: '', saved: false, loaded: false, isatvon: [] }
/** The project whose workbench is open (its id is in the URL); one module serves every project in a session. */
const projectId = () => (typeof window === 'undefined' ? undefined : /\/projects\/([^/]+)/.exec(window.location.pathname)?.[1])
const goalPath = (project: string) => `projects/${project}/goal`

let loadedFor: string | undefined = ''
let gs: GoalState = EMPTY
let etag: string | null = null
let rev = 0 // bumped by every local change, so a read that started earlier never overwrites it
const subs = new Set<() => void>()
const emit = () => subs.forEach(f => f())

function sync() {
  const project = projectId()
  if (project === loadedFor) return
  loadedFor = project
  etag = null
  rev++
  gs = { ...EMPTY, loaded: !project } // outside a project (tests, previews) there is nothing to read
  if (project) void load(project)
}

async function load(project: string) {
  const at = rev
  try {
    const { body, etag: tag } = await getJson<ProjectGoalOut>(goalPath(project))
    if (loadedFor !== project) return
    etag = tag
    if (rev !== at) return // the FDE saved or skipped meanwhile: theirs stands
    const text = body.goal ?? ''
    gs = { goal: text ? parseGoal(text) : EMPTY.goal, text, saved: !!text || gs.saved, loaded: true, isatvon: body.isatvon ?? [] }
  } catch (e) {
    if (loadedFor !== project) return
    gs = { ...gs, loaded: true, error: e instanceof Error ? e.message : String(e) }
  }
  emit()
}

function commit(next: GoalState) {
  sync()
  rev++
  gs = next
  emit()
}

async function put(project: string, goal: string | null, retry = true): Promise<void> {
  try {
    const headers: Record<string, string> = etag ? { 'if-match': etag } : {}
    const { body, etag: tag } = await putJson<ProjectGoalOut>(goalPath(project), { goal }, headers)
    if (body.goal) rescore(project)
    if (loadedFor !== project) return
    etag = tag
    if (gs.text === (body.goal ?? '')) { gs = { ...gs, isatvon: body.isatvon ?? [] }; emit() } // still the goal on screen
  } catch (e) {
    // Missing or stale ETag (another FDE or tab saved first): read the current one and try once more.
    if (retry && e instanceof ApiFailure && (e.statusCode === 412 || e.statusCode === 428)) {
      const { etag: tag } = await getJson<ProjectGoalOut>(goalPath(project))
      if (loadedFor === project) etag = tag
      return put(project, goal, false)
    }
    throw e
  }
}

/** #802: the project's files are scored against the goal; after a save they are scored again in the background.
 *  Advice only: if the request fails, the goal is still saved and the files keep their earlier scores. */
function rescore(project: string) {
  void postJson(`projects/${project}/documents/relevance/rescore`).catch(() => undefined)
}

export const getGoal = (): GoalState => { sync(); return gs }
export const useGoal = <T,>(select: (s: GoalState) => T) =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => select(getGoal()), () => select(EMPTY))

/** Saves the goal on the project. The screen moves on at once; a refusal is kept in `error` with the goal on screen. */
export async function saveGoal(raw: string): Promise<void> {
  const text = cleanGoal(raw)
  if (!text) return
  if (text.length > GOAL_MAX) {
    commit({ ...getGoal(), error: `A goal is at most ${GOAL_MAX} characters (this one has ${text.length}).` })
    return
  }
  commit({ goal: parseGoal(text), text, saved: true, loaded: true, isatvon: [] })
  const project = loadedFor
  if (!project) return
  try {
    await put(project, text)
  } catch (e) {
    if (loadedFor !== project) return
    gs = { ...gs, error: `The goal is not saved on the project yet: ${e instanceof Error ? e.message : String(e)}` }
    emit()
  }
}
/** A project that already has uploads or a pipeline run resumes where the work is: it does not ask for a goal first.
 *  Nothing is written to the project. */
export function skipGoal() { if (!getGoal().saved) commit({ ...gs, saved: true, loaded: true }) }
/** Asks for the goal again on this screen (the Upload reset); the project's stored goal is replaced on the next save. */
export const resetGoal = () => commit({ ...EMPTY, loaded: true })

/** "+ Add files" opens as soon as a goal is saved (no approval step). */
export const canAddFiles = (s: GoalState) => s.saved
/** The one lock every upload path checks (Add files menu, picker, drops, the review dialog's Add more, the uploader). */
export const addFilesLocked = () => !canAddFiles(getGoal())

/** Pane progression on Upload: 1 = the goal box alone, 2 = goal saved (files pane opens), 3 = files added (outputs pane opens). */
export const goalStage = (s: GoalState, files: number): 1 | 2 | 3 => !s.saved ? 1 : files > 0 ? 3 : 2
