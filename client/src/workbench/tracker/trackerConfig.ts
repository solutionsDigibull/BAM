// BAM progress tracker and the app's single screen registry: 4 workflow stages, each with its steps in navigation
// order. IDs are stable keys for persistence and routing, NOT display text and NOT order: screens were renamed and
// moved (Template and Translate keep IDs 4.2 and 4.3 inside Workbench; Sandbox is 4.8).
// The UI never shows the IDs. Never derive a "next step" gate from them.
import type { StepId } from '../api/types'

export const TRACKER_STATUSES = ['not_started', 'in_progress', 'needs_review', 'done', 'blocked'] as const
export type TrackerStatus = (typeof TRACKER_STATUSES)[number]

export const STATUS_LABEL: Record<TrackerStatus, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  needs_review: 'Needs review',
  done: 'Done',
  blocked: 'Blocked',
}

export interface TrackerItem {
  id: string
  /** null for a stage; the stage id for a screen. */
  parentId: string | null
  label: string
  shortLabel: string
  description: string
  /** Outline icon name (components/ui.tsx). */
  icon: string
  /** Screen URL (screens only). */
  path?: string
  /** The backend workbench step that gates (locks) this screen, if any. */
  step?: StepId
  /** Opened from another screen, not listed in the navigation (`navAs` is highlighted instead). Never counted. */
  hidden?: boolean
  navAs?: string
  /** Listed, but optional work: never counted toward its stage. */
  optional?: boolean
  /** Internal diagnostics (scan, quarantine): hidden from the FDE everywhere; the route redirects to Upload. */
  internal?: boolean
  /** Execution mode: a human checkpoint (HITL, person chip) or a step that runs on its own (Automated, bull chip); both tagged in the rail and header. */
  mode?: 'hitl' | 'automated'
}

const stage = (id: string, label: string, icon: string, description: string): TrackerItem =>
  ({ id, parentId: null, label, shortLabel: label, icon, description })
const screen = (id: string, parentId: string, label: string, icon: string, path: string, description: string, extra: Partial<TrackerItem> = {}): TrackerItem =>
  ({ id, parentId, label, shortLabel: label, icon, description, path, ...extra })

// Final workflow (15 Oct 2026, BuLLM confirmed workflow): Ingestion Upload · Standardize · Parse · Review, Context Sync ·
// Index · Schema · Verify, Workbench Scope · Grid · Template · Translate, Validation Sandbox · Check · Certify · Commit.
// Earlier screens keep their IDs and routes: they are renamed, moved, or kept as hidden details of a final step.
// `internal` screens (virus scan, quarantine, file detail) are diagnostics with no FDE role: never listed, searched
// or opened by the FDE (their old URLs redirect to Upload); the scan and quarantine still enforce every gate.
// Admin is a global destination (GLOBAL_ROUTES), not a workflow screen.
export const TRACKER_ITEMS: readonly TrackerItem[] = [
  stage('1', 'Ingestion', 'tb-inbox', 'Add the source documents, standardize and parse them, then confirm what goes to Context.'),
  screen('1.1', '1', 'Upload', 'tb-upload', '/ingestion/upload', 'Choose source files and track upload readiness.', { step: 1, mode: 'hitl' }),
  screen('1.8', '1', 'Standardize', 'tb-transform', '/ingestion/standardize', 'What the backend found in each accepted file, then start ingestion.', { mode: 'hitl' }),
  screen('1.9', '1', 'Parse', 'tb-file-code', '/ingestion/parse', 'Extract text, tables and structure from the ready files.', { mode: 'automated' }),
  screen('1.4', '1', 'Review', 'tb-clipboard-check', '/ingestion/review', 'A last look at the accepted, read files that go to Context. Not SOW approval.', { mode: 'hitl' }),
  screen('1.6', '1', 'Connectors', 'plug', '/ingestion/connectors', 'Optional. Pull documents from a connected source.', { hidden: true, navAs: '1.4', optional: true }),
  screen('1.5', '1', 'Scan', 'scan', '/ingestion/scan', 'Internal diagnostics: per-file virus scan.', { step: 1, hidden: true, internal: true, navAs: '1.1' }),
  screen('1.7', '1', 'Quarantine', 'shield', '/ingestion/quarantine', 'Internal diagnostics: files held by the scan.', { hidden: true, internal: true, navAs: '1.1' }),
  screen('1.3', '1', 'File detail', 'file', '/ingestion/file', 'Internal diagnostics: one stored file.', { hidden: true, internal: true, navAs: '1.1' }),

  stage('2', 'Context', 'tb-git-branch', 'Sync Admin Masters, index the context graph, review the schema and verify answers.'),
  screen('2.5', '2', 'Sync', 'tb-refresh', '/context/sync', 'Sync Admin Masters for context extraction.', { mode: 'hitl' }),
  screen('2.1', '2', 'Index', 'tb-search', '/rag/graph', 'Graph nodes · current graph explorer.', { mode: 'automated' }),
  screen('2.2', '2', 'Schema', 'tb-schema', '/rag/schemas', 'Schema drafts from the context graph.', { hidden: true, mode: 'automated' }),
  screen('2.3', '2', 'Verify', 'tb-shield-check', '/rag/quality', 'Context testing · Questions only.', { hidden: true, mode: 'hitl' }),
  screen('2.4', '2', 'Context readiness', 'doccheck', '/rag/review', 'Graph, schema and retrieval readiness, the knowledge bundle and provenance.', { hidden: true, navAs: '2.3' }),

  stage('3', 'Workbench', 'tb-briefcase', 'Approve the scope, review the skills grid, then the manifest and the translated code.'),
  screen('3.1', '3', 'Scope', 'tb-target', '/workbench/sow', 'SOW review and FDE approval.', { step: 1, mode: 'hitl' }),
  screen('3.2', '3', 'Grid', 'tb-table', '/workbench/skills', 'Agent > Skill review with all seven ISATVON fields.', { step: 2, mode: 'hitl' }),
  screen('4.2', '3', 'Template', 'tb-template', '/workbench/manifest', 'manifest.json, linked to the approved Scope and Grid.', { step: 3, mode: 'hitl' }),
  screen('4.3', '3', 'Translate', 'tb-code', '/workbench/code', 'Translation: the translated skill code and its translation check.', { step: 3, mode: 'hitl' }),
  screen('3.3', '3', 'Skill review', 'eye', '/workbench/review', 'One skill at a time: its contract, generated code diff and review findings (read-only).', { hidden: true, navAs: '3.2' }),
  screen('3.4', '3', 'Release prep', 'checklist', '/workbench/release', 'Manifest version, release prerequisites and version history.', { hidden: true, navAs: '4.3' }),

  stage('4', 'Validation', 'tb-shield-check', 'Run the isolated sandbox, check the results, certify the checked artifact and commit it to BAM_db.'),
  screen('4.8', '4', 'Sandbox', 'tb-box', '/validation/sandbox', 'Create and run the isolated validation environment.', { step: 4, mode: 'hitl' }),
  screen('4.4', '4', 'Check', 'tb-list-check', '/sandbox/results', 'Validation results and evidence for the current artifact.', { step: 4, mode: 'hitl' }),
  screen('4.5', '4', 'Certify', 'tb-certificate', '/sandbox/certification', 'Record approval of the checked artifact. Commit comes next.', { hidden: true, step: 4, mode: 'hitl' }),
  screen('4.6', '4', 'Commit', 'tb-database-import', '/sandbox/handover', 'Save the certified artifact to BAM_db, then deploy it to Staging.', { hidden: true, step: 4, mode: 'hitl' }),
  screen('4.1', '4', 'Validation activities', 'circle-check', '/workbench/validation', 'Check activities A–D with attempt history and findings.', { step: 3, hidden: true, navAs: '4.4' }),
  screen('4.7', '4', 'Staging complete', 'rocket', '/sandbox/staging', 'What is live on Staging. Staging complete never means production.', { step: 4, hidden: true, navAs: '4.6' }),
]

/** Global destinations outside the four stages: Admin (profile menu). */
export const GLOBAL_ROUTES = {
  admin: { path: '/admin/masters', section: 'Admin', label: 'Masters' },
} as const
export type GlobalRoute = keyof typeof GLOBAL_ROUTES
export const globalByPath = (path: string) => (Object.keys(GLOBAL_ROUTES) as GlobalRoute[]).find(k => GLOBAL_ROUTES[k].path === path)

/** Old paths keep working (bookmarks). Scan and Quarantine are internal, so their URLs open Upload. */
export const ROUTE_ALIASES: Readonly<Record<string, string>> = {
  '/ingestion': '/ingestion/upload',
  '/ingestion/queue': '/ingestion/upload',
  '/ingestion/checklist': '/ingestion/review',
  '/context': '/context/sync',
  '/validation': '/validation/sandbox',
  '/sandbox/validation': '/workbench/validation',
  '/sandbox/manifest': '/workbench/manifest',
  '/sandbox/code': '/workbench/code',
}

export const TASKS = TRACKER_ITEMS.filter(i => i.parentId === null)
export const SUBTASKS = TRACKER_ITEMS.filter(i => i.parentId !== null)
/** A stage's screens as listed in the navigation (hidden screens excluded). */
export const subtasksOf = (stageId: string) => SUBTASKS.filter(s => s.parentId === stageId && !s.hidden)
/** The screens that count toward a stage's progress (no hidden or optional ones). */
export const countedOf = (stageId: string) => subtasksOf(stageId).filter(s => !s.optional)

/** Every screen's URL and, for gated screens, the backend step that locks it. Derived from TRACKER_ITEMS. */
export interface ScreenRoute { path: string; step?: StepId }
export const SCREEN_ROUTES: Readonly<Record<string, ScreenRoute>> =
  Object.fromEntries(SUBTASKS.map(s => [s.id, { path: s.path!, ...(s.step ? { step: s.step } : {}) }]))
/** The screen where each backend workbench step is worked on. */
export const STEP_SCREEN: Readonly<Record<StepId, string>> = { 1: '3.1', 2: '3.2', 3: '4.2', 4: '4.8' }
export const screenByPath = (path: string) => {
  const item = SUBTASKS.find(s => s.path === path)
  return item ? { id: item.id, path: item.path!, step: item.step, item } : undefined
}
/** The user-facing name of a screen (never its ID). */
export const screenLabel = (id: string) => TRACKER_ITEMS.find(i => i.id === id)?.label ?? id
/** The stage a screen belongs to. */
export const stageOf = (id: string) => TASKS.find(t => t.id === SUBTASKS.find(s => s.id === id)?.parentId)

export type Statuses = Record<string, TrackerStatus>
export const initialStatuses = (): Statuses => Object.fromEntries(SUBTASKS.map(s => [s.id, 'not_started']))

/** Derived parent status; parents are never edited directly. */
export function rollup(children: readonly TrackerStatus[]): TrackerStatus {
  if (children.length > 0 && children.every(s => s === 'done')) return 'done'
  if (children.includes('blocked')) return 'blocked'
  if (children.includes('needs_review')) return 'needs_review'
  if (children.some(s => s === 'in_progress' || s === 'done')) return 'in_progress'
  return 'not_started'
}

/**
 * The one rule for what the tracker shows: workflow evidence (tracker/evidence.ts), unless the screen was set by hand
 * while its evidence was exactly what it is now. Any evidence change (a new run, a reset backend) ends the override.
 */
export function effectiveStatuses(evidence: Statuses, manual: Statuses, basis: Partial<Statuses>): Statuses {
  return Object.fromEntries(SUBTASKS.map(s => [s.id, basis[s.id] !== undefined && basis[s.id] === evidence[s.id] ? manual[s.id]! : evidence[s.id] ?? 'not_started']))
}

export const taskStatus = (stageId: string, statuses: Statuses) => rollup(countedOf(stageId).map(s => statuses[s.id] ?? 'not_started'))
export const doneCount = (items: readonly TrackerItem[], statuses: Statuses) => items.filter(s => statuses[s.id] === 'done').length
