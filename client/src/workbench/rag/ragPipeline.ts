// Stage 2 (RAG: Cognee + OKF) shared state. One store for 2.1, 2.2 and 2.3.
// The mock service simulates a run; a real Cognee/OKF client implements the same RagPipelineService.
import { useSyncExternalStore } from 'react'
import { EDGES, JOBS, NODES, SCHEMAS } from './mockData.ts'
import { cogneeRunner } from './cognee.ts'
import { layaJudgeContext, simulatedContextJudge, type ContextJudgeRequest, type ContextJudgeResult } from '../api/sowEval.ts'

export type RagStepKey = 'chunk' | 'extract' | 'graph' | 'schemas'
export type RagStepStatus = 'queued' | 'running' | 'done' | 'failed'
export type JobStatus = 'queued' | 'running' | 'done' | 'skipped' | 'failed'

export interface RagStep { key: RagStepKey; label: string; status: RagStepStatus; detail?: string; at?: string; error?: string }
export interface SchemaDraft { code: string; version: string; editedAt?: string }
export interface Handoff { at: string; schema: string; version: string; contextVersion: string }

export interface RagState {
  /** False until the service has loaded its initial state. */
  loaded: boolean
  runId?: string
  lastSyncAt?: string
  steps: RagStep[]
  jobs: JobStatus[]
  drafts: Record<string, SchemaDraft>
  handoffs: Handoff[]
  /** Real backends only: the Cognee dataset and graph size of the last run. */
  datasetId?: string
  graph?: { nodes: number; edges: number }
  /** Cognee's knowledge graph for the run (GET /api/v1/datasets/{id}/graph): entities and their relations. */
  graphData?: KnowledgeGraph
  /** Which engine ran the pipeline. */
  engine: 'simulated' | 'cognee'
  /** Jev System-1 context sufficiency evaluation of the ingested IP data. */
  contextEvaluation?: import('../api/sowEval.ts').ContextJudgeResult
  contextEvalStatus?: 'idle' | 'running' | 'done' | 'failed'
  /** The run whose Context stage the FDE started (Sync's Start) and approved (Verify's Approve). The pipeline builds in
   *  the background after Parse; the Context steps only count once a person has started / approved them for this run. */
  contextStartedRun?: string
  verifiedRun?: string
}


export interface KnowledgeGraph { nodes: { id: string; label: string; type: string }[]; edges: { source: string; target: string; label: string }[] }
export interface Relation { from: string; fromType: string; label: string; to: string; toType: string }

/** Entity relations of the run: Cognee's graph when it ran live, else the EXAMPLE fixture graph. */
export function relations(s: Pick<RagState, 'graphData'>): Relation[] {
  const g: KnowledgeGraph = s.graphData ?? {
    nodes: NODES.map(n => ({ id: n.id, label: n.label + (n.sub ?? ''), type: n.okfClass })),
    edges: EDGES.map(e => ({ source: e.source, target: e.target, label: e.label ?? 'related_to' })),
  }
  const byId = new Map(g.nodes.map(n => [n.id, n]))
  return g.edges.flatMap(e => {
    const a = byId.get(e.source), b = byId.get(e.target)
    return a && b ? [{ from: a.label, fromType: a.type, label: e.label, to: b.label, toType: b.type }] : []
  })
}
/** Discovery facts the SOW is judged against: entity facts plus relation triples ("A has_document B"). */
export function contextFacts(s: Pick<RagState, 'graphData'>): string[] {
  const rel = relations(s).map(r => `${r.from} ${r.label.replace(/_/g, ' ')} ${r.to}`)
  return s.graphData ? rel : [...NODES.map(n => `${n.label}${n.sub ?? ''}: ${n.facts}`), ...rel]
}

/** What stage 1 hands to RAG: the OKF v0.2 source concepts and the released object URIs. */
export interface RagInput { files: { path: string; content: string }[]; objects: string[] }

/** Runs (or resumes) a pipeline from a step. `io` is the shared store; `live()` is false once a newer run replaced this one. */
export type RagRunner = (from: RagStepKey, runId: string, io: {
  get: () => RagState; set: (p: Partial<RagState>) => void; setStep: (k: RagStepKey, p: Partial<RagStep>) => void
  live: () => boolean; input?: RagInput; failAt?: RagStepKey; sleep: (ms: number) => Promise<void>
}) => Promise<void>

export interface RagPipelineService {
  getState(): RagState
  subscribe(listener: () => void): () => void
  /** Stage 1 hand-off starts a run with the released input. `failAt` simulates a failing step (mock only). */
  start(opts?: { failAt?: RagStepKey; input?: RagInput }): void
  retry(step: RagStepKey): void
  saveSchemaDraft(name: string, code: string): void
  /** Creates a Workbench draft from one schema. Never approves anything. */
  sendToWorkbench(name: string): Handoff
  /** Evaluates whether all ingested IP documents and discovery facts provide sufficient context. */
  evaluateContext(customInput?: Partial<ContextJudgeRequest>): Promise<ContextJudgeResult>
  setContextEvaluation(res: ContextJudgeResult): void
  /** Sync's Start / Verify's Approve for the current run. */
  markContextStarted(): void
  markVerified(): void
}

const STEP_DEFS: [RagStepKey, string][] = [
  ['chunk', 'Chunk + embed'], ['extract', 'Entity extraction'], ['graph', 'Graph build (Context Engine)'], ['schemas', 'Schema proposals'],
]
const now = () => new Date().toISOString()
const freshSteps = (): RagStep[] => STEP_DEFS.map(([key, label]) => ({ key, label, status: 'queued' }))
const bump = (v: string) => `v0.${Number(v.split('.')[1]) + 1}`

export function createMockRagService({ speed = 1, loadDelay = 300, runner, engine = 'simulated' }: { speed?: number; loadDelay?: number; runner?: RagRunner; engine?: RagState['engine'] } = {}): RagPipelineService {
  let state: RagState = {
    loaded: false, engine, steps: freshSteps(), jobs: JOBS.map(() => 'queued'), handoffs: [],
    drafts: Object.fromEntries(SCHEMAS.map(s => [s.name, { code: s.code, version: 'v0.3' }])),
  }
  const subs = new Set<() => void>()
  const set = (patch: Partial<RagState>) => { state = { ...state, ...patch }; subs.forEach(f => f()) }
  const setStep = (key: RagStepKey, patch: Partial<RagStep>) =>
    set({ steps: state.steps.map(s => (s.key === key ? { ...s, ...patch } : s)) })
  const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms * speed))

  async function evaluateContext(customInput?: Partial<ContextJudgeRequest>): Promise<ContextJudgeResult> {
    set({ contextEvalStatus: 'running' })
    const facts = contextFacts(state)
    const entities = NODES.map(n => ({ name: n.label, type: n.okfClass }))
    const req: ContextJudgeRequest = {
      sources: customInput?.sources ?? (input?.objects.map(o => ({ name: o, type: o.split('.').pop()?.toUpperCase() })) ?? JOBS.map(j => ({ name: j.object, type: j.okfClass }))),
      facts: customInput?.facts ?? facts,
      entities: customInput?.entities ?? entities,
      schemas: customInput?.schemas ?? SCHEMAS.map(s => s.name),
    }
    try {
      const res = await layaJudgeContext(req)
      set({ contextEvaluation: res, contextEvalStatus: 'done' })
      return res
    } catch {
      const res = simulatedContextJudge(req)
      set({ contextEvaluation: res, contextEvalStatus: 'done' })
      return res
    }
  }

  setTimeout(() => {
    set({ loaded: true })
    void evaluateContext()
  }, loadDelay * speed)

  let failAt: RagStepKey | undefined
  let input: RagInput | undefined
  async function runFrom(from: RagStepKey, runId: string) {
    if (runner) return runner(from, runId, { get: () => state, set, setStep, live: () => state.runId === runId, input, failAt, sleep })
    const live = () => state.runId === runId
    for (const [key] of STEP_DEFS.slice(STEP_DEFS.findIndex(([k]) => k === from))) {
      setStep(key, { status: 'running', at: now(), error: undefined, detail: undefined })
      if (key === 'extract') {
        // One object at a time so 2.1 and 2.3 fill in live.
        for (let i = 0; i < JOBS.length; i++) {
          if (state.jobs[i] === 'done' || state.jobs[i] === 'skipped') continue
          set({ jobs: state.jobs.map((j, n) => (n === i ? 'running' : j)) })
          await sleep(650)
          if (!live()) return
          if (failAt === 'extract' && i === 2) {
            set({ jobs: state.jobs.map((j, n) => (n === i ? 'failed' : j)) })
            setStep('extract', { status: 'failed', at: now(), error: `Extraction failed on ${JOBS[i].object} (simulated). Retry re-runs from this object.` })
            failAt = undefined
            return
          }
          set({ jobs: state.jobs.map((j, n) => (n === i ? (JOBS[i].okfClass === 'Unclassified' ? 'skipped' : 'done') : j)) })
        }
      } else {
        await sleep(key === 'graph' ? 2400 : key === 'chunk' ? 1800 : 1400)
        if (!live()) return
        if (failAt === key) {
          setStep(key, { status: 'failed', at: now(), error: `${STEP_DEFS.find(d => d[0] === key)![1]} failed (simulated). Retry re-runs this step.` })
          failAt = undefined
          return
        }
      }
      const entities = JOBS.reduce((n, j, i) => n + (state.jobs[i] === 'done' ? j.entities : 0), 0)
      const detail = { chunk: `${JOBS.length} objects`, extract: `${entities} entities`, graph: '385 relations', schemas: `${SCHEMAS.length} drafts ready` }[key]
      setStep(key, { status: 'done', at: now(), detail })
    }
    set({ lastSyncAt: now() })
    void evaluateContext()
  }

  return {
    getState: () => state,
    subscribe: l => { subs.add(l); return () => { subs.delete(l) } },
    start(opts = {}) {
      failAt = opts.failAt
      input = opts.input ?? input
      const runId = `rag-${crypto.randomUUID().slice(0, 8)}`
      set({ runId, steps: freshSteps(), jobs: JOBS.map(() => 'queued') })
      void runFrom('chunk', runId)
    },
    retry(step) {
      if (state.steps.find(s => s.key === step)?.status !== 'failed' || !state.runId) return
      if (step === 'extract') set({ jobs: state.jobs.map(j => (j === 'failed' ? 'queued' : j)) })
      void runFrom(step, state.runId)
    },
    saveSchemaDraft(name, code) {
      const d = state.drafts[name]
      if (!d || d.code === code) return
      set({ drafts: { ...state.drafts, [name]: { code, version: bump(d.version), editedAt: now() } } })
    },
    sendToWorkbench(name) {
      const d = state.drafts[name]
      if (!d) throw new Error(`Unknown schema ${name}`)
      if (state.steps[3].status !== 'done' || !state.runId) throw new Error('Schema proposals are not ready')
      const h: Handoff = { at: now(), schema: name, version: d.version, contextVersion: state.runId }
      set({ handoffs: [...state.handoffs, h] })
      return h
    },
    evaluateContext,
    setContextEvaluation(res: ContextJudgeResult) {
      set({ contextEvaluation: res, contextEvalStatus: 'done' })
    },
    markContextStarted() { if (state.runId) set({ contextStartedRun: state.runId }) },
    markVerified() { if (state.runId) set({ verifiedRun: state.runId }) },
  }
}


// VITE_RAG_BACKEND=cognee runs the pipeline on a real Cognee server (proxied at /cognee by Vite; see vite.config.ts).
const env = (import.meta as { env?: Record<string, string | undefined> }).env ?? {}
export const ragPipeline: RagPipelineService = env.VITE_RAG_BACKEND === 'cognee'
  ? createMockRagService({ runner: cogneeRunner(env.VITE_COGNEE_URL || '/cognee'), engine: 'cognee' })
  : createMockRagService()

export const useRag = <T,>(select: (s: RagState) => T) =>
  useSyncExternalStore(ragPipeline.subscribe, () => select(ragPipeline.getState()))

export const isRunning = (s: RagState) => s.steps.some(x => x.status === 'running')
export const stepStatus = (s: RagState, key: RagStepKey) => s.steps.find(x => x.key === key)!.status

/** One fixed viewport per canvas size: the root's centre lands on the canvas centre, and the scale fits the farthest
 *  node (centre distance + radius) from the root, with padding. No fitView: a bounding-box fit would move the root. */
export function centredViewport(pts: { x: number; y: number; r: number }[], root: { x: number; y: number }, w: number, h: number, pad = 28, maxZoom = 1.2) {
  let ex = 1, ey = 1
  for (const p of pts) { ex = Math.max(ex, Math.abs(p.x - root.x) + p.r); ey = Math.max(ey, Math.abs(p.y - root.y) + p.r) }
  const zoom = Math.max(0.05, Math.min(maxZoom, (w / 2 - pad) / ex, (h / 2 - pad) / ey))
  return { x: w / 2 - root.x * zoom, y: h / 2 - root.y * zoom, zoom }
}
