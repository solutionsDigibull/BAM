// Stage 2 (knowledge) shared state. One store for 2.1, 2.2 and 2.3.
// The host fills it from the SBAM knowledge endpoints (`setRagState`); the workbench runs no pipeline of its own.
import { useSyncExternalStore } from 'react'

export type RagStepKey = 'chunk' | 'extract' | 'graph'
export type RagStepStatus = 'queued' | 'running' | 'done' | 'failed'
export type KnowledgeStatus = 'BUILDING' | 'READY' | 'FAILED' | 'OUTDATED'

export interface RagStep { key: RagStepKey; label: string; status: RagStepStatus; detail?: string; at?: string; error?: string }

/** One source document of the knowledge version, as its coverage row reports it. */
export interface RagDoc { id: string; title: string; status: string; chunks: number; entities: number; reason?: string }

export interface KnowledgeGraph { nodes: { id: string; label: string; type: string; degree?: number }[]; edges: { source: string; target: string; label: string }[] }
export interface Relation { from: string; fromType: string; label: string; to: string; toType: string }

export interface RagState {
  /** False until the host has loaded the knowledge state. */
  loaded: boolean
  /** The knowledge version shown (its id). Absent while none was ever built. */
  runId?: string
  version?: number
  status?: KnowledgeStatus
  error?: string
  lastSyncAt?: string
  counts: { documents: number; entities: number; relations: number; chunks: number }
  /** Admin masters the version includes: how many records and how many master documents. */
  masters?: { included: number; documents: number }
  docs: RagDoc[]
  steps: RagStep[]
  /** The knowledge graph of the version (entities and their relations). */
  graphData?: KnowledgeGraph
}

const STEP_LABELS: Record<RagStepKey, string> = { chunk: 'Chunk + embed', extract: 'Entity extraction', graph: 'Graph build' }
const STEP_KEYS: RagStepKey[] = ['chunk', 'extract', 'graph']

/** The three steps of a knowledge version, derived from its status and counts (the backend reports no per-step state). */
export function stepsOf(status: KnowledgeStatus | undefined, counts: RagState['counts'], error?: string, at?: string): RagStep[] {
  const reached: Record<RagStepKey, boolean> = { chunk: counts.chunks > 0, extract: counts.entities > 0, graph: counts.relations > 0 }
  const detail: Record<RagStepKey, string> = { chunk: `${counts.chunks} chunks`, extract: `${counts.entities} entities`, graph: `${counts.relations} relations` }
  const done = status === 'READY' || status === 'OUTDATED'
  let firstOpen = true
  return STEP_KEYS.map((key): RagStep => {
    const label = STEP_LABELS[key]
    if (done || (status && reached[key])) return { key, label, status: 'done', detail: detail[key], at }
    if (!status) return { key, label, status: 'queued' }
    const mine = firstOpen
    firstOpen = false
    if (status === 'FAILED') return mine ? { key, label, status: 'failed', error: error ?? 'The build failed.', at } : { key, label, status: 'queued' }
    return { key, label, status: mine ? 'running' : 'queued' }
  })
}

const noCounts = (): RagState['counts'] => ({ documents: 0, entities: 0, relations: 0, chunks: 0 })

const empty = (): RagState => ({ loaded: false, counts: noCounts(), docs: [], steps: stepsOf(undefined, noCounts()) })

/** Entity relations of the version, from the graph the backend returned (none before one exists). */
export function relations(s: Pick<RagState, 'graphData'>): Relation[] {
  const g = s.graphData
  if (!g) return []
  const byId = new Map(g.nodes.map(n => [n.id, n]))
  return g.edges.flatMap(e => {
    const a = byId.get(e.source)
    const b = byId.get(e.target)
    return a && b ? [{ from: a.label, fromType: a.type, label: e.label, to: b.label, toType: b.type }] : []
  })
}

/** What the host lets a screen do about the knowledge build; absent actions are hidden by the screens. */
export interface RagActions {
  /** Starts a knowledge build for the verified documents (POST knowledge/rebuild). */
  start?: () => void | Promise<void>
  /** Retries the failed build (the same rebuild: the backend has no per-step retry). */
  retry?: () => void | Promise<void>
  /** Downloads the version's OKF bundle (GET knowledge/{id}/okf-bundle). */
  downloadBundle?: () => void | Promise<void>
}

export interface RagPipelineService {
  getState(): RagState
  subscribe(listener: () => void): () => void
  start(): void
  retry(step?: RagStepKey): void
  /** True when the host can start or retry a build. */
  canStart(): boolean
}

let state: RagState = empty()
let actions: RagActions = {}
const subs = new Set<() => void>()
const emit = () => subs.forEach(f => f())

/** The host replaces the state after every poll; the store keeps no state of its own. */
export function setRagState(next: Partial<RagState> & { loaded: boolean }): void {
  const merged: RagState = { ...empty(), ...next }
  merged.steps = next.steps ?? stepsOf(merged.status, merged.counts, merged.error, merged.lastSyncAt)
  state = merged
  emit()
}

export const ragActions = (): RagActions => actions

export function setRagActions(next: RagActions): void {
  actions = next
  emit()
}

export const ragPipeline: RagPipelineService = {
  getState: () => state,
  subscribe: l => {
    subs.add(l)
    return () => {
      subs.delete(l)
    }
  },
  start() {
    void actions.start?.()
  },
  retry() {
    void (actions.retry ?? actions.start)?.()
  },
  canStart: () => !!(actions.start ?? actions.retry),
}

const serverState = empty()
export const useRag = <T,>(select: (s: RagState) => T): T =>
  useSyncExternalStore(ragPipeline.subscribe, () => select(ragPipeline.getState()), () => select(serverState))

export const isRunning = (s: RagState): boolean => s.steps.some(x => x.status === 'running')
export const stepStatus = (s: RagState, key: RagStepKey): RagStepStatus => s.steps.find(x => x.key === key)?.status ?? 'queued'

/** One fixed viewport per canvas size: the root's centre lands on the canvas centre, and the scale fits the farthest
 *  node (centre distance + radius) from the root, with padding. No fitView: a bounding-box fit would move the root. */
export function centredViewport(pts: { x: number; y: number; r: number }[], root: { x: number; y: number }, w: number, h: number, pad = 28, maxZoom = 1.2) {
  let ex = 1
  let ey = 1
  for (const p of pts) {
    ex = Math.max(ex, Math.abs(p.x - root.x) + p.r)
    ey = Math.max(ey, Math.abs(p.y - root.y) + p.r)
  }
  const zoom = Math.max(0.05, Math.min(maxZoom, (w / 2 - pad) / ex, (h / 2 - pad) / ey))
  return { x: w / 2 - root.x * zoom, y: h / 2 - root.y * zoom, zoom }
}
