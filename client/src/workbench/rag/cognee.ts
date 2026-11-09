// Real Cognee (https://github.com/topoteretes/cognee) runner for the stage 2 pipeline, against its HTTP API:
//   POST /api/v1/add                  multipart: data[] (OKF v0.2 concept files), raw_data[] (released s3:// objects), datasetName
//   POST /api/v1/cognify              { datasets, run_in_background: true }
//   GET  /api/v1/datasets/status      ?dataset=<id>  -> { <id>: DATASET_PROCESSING_STARTED | _COMPLETED | _ERRORED }
//   GET  /api/v1/datasets/<id>/graph  -> { nodes[], edges[] }
// Run the server with REQUIRE_AUTHENTICATION=false locally (or put a token behind the Vite proxy), and give it
// S3 read access so it can fetch the released objects. Schema proposals stay template drafts (Cognee does not emit Pydantic schemas).
import type { KnowledgeGraph, RagRunner, RagStepKey } from './ragPipeline.ts'
import { JOBS, SCHEMAS } from './mockData.ts'

export const COGNEE_DATASET = 'bam-rfq'
const now = () => new Date().toISOString()

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw new Error(`${init?.method ?? 'GET'} ${url.replace(/^https?:\/\/[^/]+/, '')} -> HTTP ${res.status}${await res.text().then(t => (t ? `: ${t.slice(0, 160)}` : ''), () => '')}`)
  return res.json() as Promise<T>
}

export function cogneeRunner(base: string, { dataset = COGNEE_DATASET, pollMs = 2000, maxPolls = 900 } = {}): RagRunner {
  return async (from, _runId, io) => {
    const fail = (key: RagStepKey, e: unknown) => io.setStep(key, { status: 'failed', at: now(), error: `Context Engine: ${(e as Error).message}` })
    const order: RagStepKey[] = ['chunk', 'extract', 'graph', 'schemas']
    const runs = (k: RagStepKey) => order.indexOf(k) >= order.indexOf(from)
    let datasetId = io.get().datasetId

    if (runs('chunk')) {
      io.setStep('chunk', { status: 'running', at: now(), error: undefined, detail: undefined })
      const files = io.input?.files ?? [], objects = io.input?.objects ?? []
      try {
        if (!files.length && !objects.length) throw new Error('nothing to add: no released documents were handed over')
        const form = new FormData()
        for (const f of files) form.append('data', new Blob([f.content], { type: 'text/markdown' }), f.path.replaceAll('/', '__'))
        for (const o of objects) form.append('raw_data', o)
        form.append('datasetName', dataset)
        datasetId = (await call<{ dataset_id: string }>(`${base}/api/v1/add`, { method: 'POST', body: form })).dataset_id
      } catch (e) { return fail('chunk', e) }
      if (!io.live()) return
      io.set({ datasetId })
      io.setStep('chunk', { status: 'done', at: now(), detail: `${files.length} OKF concepts + ${objects.length} objects added` })
    }

    if (runs('extract')) {
      io.setStep('extract', { status: 'running', at: now(), error: undefined, detail: undefined })
      try {
        await call(`${base}/api/v1/cognify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ datasets: [dataset], run_in_background: true }) })
        for (let i = 0; ; i++) {
          if (i >= maxPolls) throw new Error('cognify did not finish in time')
          await new Promise(r => setTimeout(r, pollMs))
          if (!io.live()) return
          const st = (await call<Record<string, string>>(`${base}/api/v1/datasets/status?dataset=${datasetId}`))[datasetId!]
          if (st === 'DATASET_PROCESSING_COMPLETED') break
          if (st === 'DATASET_PROCESSING_ERRORED') throw new Error('the cognify pipeline failed (see the Cognee server log)')
        }
      } catch (e) { return fail('extract', e) }
      io.set({ jobs: JOBS.map(() => 'done') })
      io.setStep('extract', { status: 'done', at: now(), detail: 'cognify completed' })
    }

    if (runs('graph')) {
      io.setStep('graph', { status: 'running', at: now(), error: undefined })
      try {
        const g = await call<KnowledgeGraph>(`${base}/api/v1/datasets/${datasetId}/graph`)
        // Keep the entities and their relations (capped for the browser); 2.1, the OKF bundle and the SOW judge use them.
        const nodes = g.nodes.slice(0, 400).map(n => ({ id: String(n.id), label: n.label, type: n.type }))
        const keep = new Set(nodes.map(n => n.id))
        const edges = g.edges.filter(e => keep.has(String(e.source)) && keep.has(String(e.target))).map(e => ({ source: String(e.source), target: String(e.target), label: e.label }))
        io.set({ graph: { nodes: g.nodes.length, edges: g.edges.length }, graphData: { nodes, edges } })
        io.setStep('graph', { status: 'done', at: now(), detail: `${g.nodes.length} nodes · ${g.edges.length} relations` })
      } catch (e) { return fail('graph', e) }
    }

    io.setStep('schemas', { status: 'done', at: now(), detail: `${SCHEMAS.length} template drafts` })
    io.set({ lastSyncAt: now() })
  }
}
