// Stage 2 (knowledge) screens. Everything shown comes from the knowledge version the host loaded into `ragPipeline`;
// nothing here is sample data and nothing here approves or deploys.
import { memo, useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './rag.css'
import { useUi } from '../lib/store'
import { RAG_ROUTES, goScreen } from '../lib/route'
import type { Snapshot } from '../api/types'
import { Dialog, Empty, ErrorState, Icon, Skeleton, StatusPill, fmtTime } from '../components/ui'
import { centredViewport, isRunning, ragActions, ragPipeline, relations, stepStatus, useRag, type RagState, type RagStepStatus } from './ragPipeline'

// While the version builds, the graph explorer shows the build itself (version, progress, the steps);
// the graph follows once it finishes.
function GraphOrBuild() {
  const s = useRag(x => x)
  const building = !!s.runId && !s.steps.every(x => x.status === 'done')
  if (!building) return <GraphExplorer />
  const done = s.steps.filter(x => x.status === 'done').length + s.steps.filter(x => x.status === 'running').length * 0.5
  const ICON: Record<string, { i: string; c: string; t: string }> = {
    done: { i: 'check', c: 'tone-ok', t: 'Done' },
    running: { i: 'spinner', c: 'tone-info', t: 'In progress' },
    failed: { i: 'alert', c: 'tone-err', t: 'Failed' },
    queued: { i: 'circle', c: 'muted', t: 'Queued' },
  }
  return (
    <section className="ing dc rag-build" aria-labelledby="center-title">
      <header className="dc-head"><div className="dc-head-text"><h2 id="center-title" tabIndex={-1}>Knowledge build</h2><p className="muted sm mono">{s.version ? `Version ${s.version}` : s.runId}</p></div></header>
      <div className="v3-meter" role="progressbar" aria-valuemin={0} aria-valuemax={s.steps.length} aria-valuenow={done} aria-label="Knowledge build progress"><span style={{ width: `${(done / s.steps.length) * 100}%` }} /></div>
      <ul className="dc-list dc-steps">
        {s.steps.map(x => {
          const m = ICON[x.status] ?? ICON.queued!
          return (
            <li key={x.key}>
              <span className={m.c}><Icon name={m.i} size={15} spin={m.i === 'spinner'} /></span>
              <div><strong>{x.label}</strong>{x.error && <span className="stage-error xs">{x.error}</span>}</div>
              <span className="muted xs">{x.status === 'done' && x.detail ? x.detail : m.t}</span>
              {x.status === 'failed' && ragPipeline.canStart() && <button className="btn btn-outline sm" onClick={() => ragPipeline.retry(x.key)}><Icon name="refresh" />Retry</button>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// Navigation between the stage-2 screens is the tracker's stage 2 panel (one navigation for the whole app).
// Only the graph explorer exists: schema drafts, context testing and readiness review have no backend.
export function RagScreen({ path }: { path: string; snap?: Snapshot }) {
  const loaded = useRag(s => s.loaded)
  return (
    <div className={`rag ${path === RAG_ROUTES.graph ? 'rag-graph-page' : ''}`}>
      {!loaded ? <div className="rag-card"><Skeleton rows={8} label="Loading the knowledge graph" /></div>
        : path === RAG_ROUTES.graph ? <GraphOrBuild />
        : <Empty icon="info" title="Not available">This screen has no backend yet.</Empty>}
    </div>
  )
}

// ---- shared ---------------------------------------------------------------------
const STEP_ICON: Record<RagStepStatus, { icon: string; cls: string; text: string; spin?: boolean }> = {
  queued: { icon: 'circle', cls: 'q', text: 'Queued' },
  running: { icon: 'spinner', cls: 'r', text: 'Running', spin: true },
  done: { icon: 'check', cls: 'd', text: 'Done' },
  failed: { icon: 'x', cls: 'f', text: 'Failed' },
}

/** `lead` adds a gate row before the knowledge steps (the virus scan); `controls` replaces the re-run control. */
export function PipelineCard({ title = 'Build pipeline', lead, controls }: { title?: string; lead?: ReactNode; controls?: ReactNode }) {
  const s = useRag(x => x)
  const running = isRunning(s)
  return (
    <section className="rag-card" aria-labelledby="pipe-title">
      <header className="rag-card-head"><h3 id="pipe-title">{title}</h3>{s.version && <span className="mono xs muted">Version {s.version}</span>}</header>
      <ol className="pipe">
        {lead}
        {s.steps.map(st => {
          const ic = STEP_ICON[st.status]
          return (
            <li key={st.key} className={`pipe-step ps-${ic.cls}`}>
              <span className="pipe-icon"><Icon name={ic.icon} size={13} spin={ic.spin} /><span className="sr-only">{ic.text}</span></span>
              <span className="pipe-label">{st.label}</span>
              <span className="pipe-detail">{st.detail ?? (st.status === 'queued' ? 'queued' : '')}{st.status === 'done' && st.at && <span className="muted"> · {fmtTime(st.at)}</span>}</span>
              {st.status === 'failed' && <>
                <p className="stage-error pipe-error"><Icon name="alert" size={12} /> {st.error}</p>
                {ragPipeline.canStart() && <button className="btn btn-secondary sm pipe-retry" onClick={() => ragPipeline.retry(st.key)}><Icon name="refresh" />Retry</button>}
              </>}
            </li>
          )
        })}
      </ol>
      {controls ?? (!running && (
        <div className="pipe-controls">
          {/* The first build starts from the upload step once every virus scan passes; this button is only for rebuilds. */}
          {s.runId
            ? ragPipeline.canStart() && <button className="btn btn-secondary sm" onClick={() => ragPipeline.start()}><Icon name="refresh" />Rebuild</button>
            : <span className="muted xs"><Icon name="clock" size={11} /> Starts from Upload once every virus scan passes.</span>}
        </div>
      ))}
    </section>
  )
}

// ---- entity relations (graph edges) --------------------------------------------------
const KIND_OF: Record<string, string> = {
  request: 'request',
  document: 'document',
  table: 'table',
  cadgeometry: 'cad',
  correspondence: 'correspondence',
  material: 'material',
  customer: 'customer',
}

/** Every relation in the version's knowledge graph, by label. */
function RelationsCard({ focus, onSelectEntity }: { focus?: string; onSelectEntity?: (name: string) => void }) {
  const s = useRag(x => x)
  const [label, setLabel] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const done = stepStatus(s, 'graph') === 'done'
  const all = done ? relations(s) : []
  const counts = [...all.reduce((m, r) => m.set(r.label, (m.get(r.label) ?? 0) + 1), new Map<string, number>())].sort((a, b) => b[1] - a[1])

  const q = query.trim().toLowerCase()
  const shown = all.filter(r => {
    if (label && r.label !== label) return false
    if (focus && !r.from.includes(focus) && !r.to.includes(focus) && !focus.includes(r.from) && !focus.includes(r.to)) return false
    if (q) {
      return (
        r.from.toLowerCase().includes(q) ||
        r.to.toLowerCase().includes(q) ||
        r.label.toLowerCase().includes(q) ||
        r.fromType.toLowerCase().includes(q) ||
        r.toType.toLowerCase().includes(q)
      )
    }
    return true
  })

  return (
    <section className="rag-card" aria-labelledby="rel-title">
      <header className="rag-card-head">
        <h3 id="rel-title">Entity relations</h3>
        <span className="tag">{all.length} triples</span>
      </header>
      {!done ? (
        <p className="muted sm">Relations appear when the graph build finishes.</p>
      ) : (
        <>
          <div className="rel-search-row">
            <input
              type="text"
              className="rel-search-input"
              placeholder="Search entity or relation"
              value={query}
              onChange={e => setQuery(e.target.value)}
              aria-label="Filter entity relations"
            />
            {query && (
              <button className="rel-clear-btn" onClick={() => setQuery('')} title="Clear filter">
                <Icon name="x" size={11} />
              </button>
            )}
          </div>
          <div className="filter-chips" role="group" aria-label="Filter relations by type">
            <button className="class-chip" aria-pressed={!label} onClick={() => setLabel(null)}>
              All {all.length}
            </button>
            {counts.map(([l, n]) => (
              <button key={l} className="class-chip" aria-pressed={label === l} onClick={() => setLabel(label === l ? null : l)}>
                {l.replace(/_/g, ' ')} {n}
              </button>
            ))}
          </div>
          {focus && (
            <p className="muted xs">
              Relations connected to <strong>{focus}</strong>. Clear the selection in the graph to see all.
            </p>
          )}
          <ul className="rel-list">
            {shown.slice(0, 60).map((r, i) => (
              <li key={i} className="rel-row" onClick={() => onSelectEntity?.(r.from)}>
                <span className={focus && (r.from.includes(focus) || focus.includes(r.from)) ? 'rel-self' : ''} title={r.fromType}>
                  {r.from}
                </span>
                <span className="rel-label">{r.label.replace(/_/g, ' ')}</span>
                <span className={focus && (r.to.includes(focus) || focus.includes(r.to)) ? 'rel-self' : ''} title={r.toType}>
                  {r.to}
                </span>
              </li>
            ))}
          </ul>
          {shown.length > 60 && <p className="muted xs">Showing 60 of {shown.length}.</p>}
        </>
      )}
    </section>
  )
}

// ---- Graph explorer ---------------------------------------------------------------
type OkfNodeData = {
  label: string
  sub?: string
  kind: string
  selected: boolean
  neighbor?: boolean
  dimmed?: boolean
}

function OkfNode({ data }: NodeProps<Node<OkfNodeData>>) {
  return (
    <div
      className={`okf-node k-${data.kind} ${data.selected ? 'sel' : ''} ${data.neighbor ? 'neighbor' : ''} ${data.dimmed ? 'dimmed' : ''}`}
      title={`${data.label}${data.sub ? ` (${data.sub})` : ''} · ${data.kind}`}
    >
      <Handle type="target" position={Position.Top} className="okf-handle" />
      <span className="okf-label">{data.label}</span>
      {data.sub && <span className="okf-sub">{data.sub}</span>}
      <Handle type="source" position={Position.Bottom} className="okf-handle" />
    </div>
  )
}
const nodeTypes = { okf: OkfNode }
const radius = (kind: string) => (kind === 'request' ? 48 : 34)
// Read-only, fixed canvas: no fitView, zoom, pan, drag or keyboard auto-pan; the viewport is computed, never input-driven.
const LOCKED = { nodesDraggable: false, nodesConnectable: false, panOnDrag: false, panOnScroll: false, zoomOnScroll: false, zoomOnPinch: false,
  zoomOnDoubleClick: false, autoPanOnNodeFocus: false, selectionKeyCode: null, multiSelectionKeyCode: null } as const

/** Canvas content size from a ResizeObserver; sub-pixel and identical measurements are ignored (no write, no loop). */
function useCanvasSize() {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const ro = useRef<ResizeObserver | null>(null)
  const ref = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect()
    if (!el) return
    ro.current = new ResizeObserver(([e]) => {
      if (!e) return
      const { width: w, height: h } = e.contentRect
      setSize(s => (s && Math.abs(s.w - w) < 1 && Math.abs(s.h - h) < 1) || !w || !h ? s : { w, h })
    })
    ro.current.observe(el)
  }, [])
  return [ref, size] as const
}

// Layout: the best-connected entity goes at the centre and the rest on a ring grouped by type.
type Laid = { id: string; x: number; y: number; r: number; label: string; sub?: string; kind: string }
const NO_ROOT: Laid = { id: '', x: 400, y: 300, r: 0, label: '', kind: '' }
function layout(graph: RagState['graphData']): { all: Laid[]; root: Laid; edges: { source: string; target: string; label?: string }[] } {
  if (!graph || !graph.nodes.length) return { all: [], root: NO_ROOT, edges: [] }
  const deg = new Map<string, number>()
  for (const e of graph.edges) for (const id of [e.source, e.target]) deg.set(id, (deg.get(id) ?? 0) + 1)
  const rootId = [...graph.nodes].sort((a, b) => (deg.get(b.id) ?? 0) - (deg.get(a.id) ?? 0))[0]?.id
  const ring = graph.nodes.filter(n => n.id !== rootId).sort((a, b) => a.type.localeCompare(b.type))
  const R = Math.max(220, ring.length * 14)
  const kind = (t: string) => KIND_OF[t.toLowerCase()] ?? 'document'
  const all = graph.nodes.map(n => {
    const isRoot = n.id === rootId
    const a = (2 * Math.PI * ring.indexOf(n)) / Math.max(1, ring.length)
    return { id: n.id, x: isRoot ? 400 : 400 + R * Math.cos(a), y: isRoot ? 300 : 300 + R * Math.sin(a), r: isRoot ? 48 : 34, label: n.label, sub: n.type, kind: isRoot ? 'request' : kind(n.type) }
  })
  return { all, root: all.find(n => n.id === rootId) ?? NO_ROOT, edges: graph.edges }
}

// memo: the snapshot poll re-renders the shell; the graph only renders for its own store, filters or selection.
const GraphExplorer = memo(function GraphExplorer() {
  const s = useRag(x => x)
  const theme = useUi(x => x.theme)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [dlg, setDlg] = useState<'info' | 'list' | null>(null)
  const [relQ, setRelQ] = useState('')
  const [boxRef, size] = useCanvasSize()
  const chunk = stepStatus(s, 'chunk')
  const graph = stepStatus(s, 'graph')
  const live = s.graphData
  const laid = useMemo(() => layout(live), [live])
  const classCounts = useMemo(() => [...(live?.nodes ?? []).reduce((m, n) => m.set(n.type, (m.get(n.type) ?? 0) + 1), new Map<string, number>())].sort((a, b) => b[1] - a[1]), [live])
  // Scale comes from every laid-out node, not just the visible ones, so filters never rescale.
  const base = useMemo(() => size && laid.all.length ? centredViewport(laid.all, laid.root, size.w, size.h) : null, [laid, size])
  // Zoom scales around the canvas centre, where the parent node sits, so the parent never moves.
  const viewport = useMemo(() => base && size && { zoom: base.zoom * zoom, x: size.w / 2 - (size.w / 2 - base.x) * zoom, y: size.h / 2 - (size.h / 2 - base.y) * zoom }, [base, size, zoom])

  const typeOf = useMemo(() => new Map((live?.nodes ?? []).map(n => [n.id, n.type])), [live])
  const visibleKey = laid.all.filter(n => !hidden.has(typeOf.get(n.id) ?? '')).map(n => n.id).join()
  // Relationship tracing: the selected node, its neighbours and their edges stay lit; everything else dims.
  const near = useMemo(() => {
    if (!selected) return null
    const ids = new Set([selected])
    for (const e of laid.edges) { if (e.source === selected) ids.add(e.target); if (e.target === selected) ids.add(e.source) }
    return ids
  }, [laid, selected])
  const nodes = useMemo<Node<OkfNodeData>[]>(() => {
    const ids = new Set(visibleKey.split(','))
    // Fixed width/height: React Flow never has to re-measure, so nodes never render hidden for a frame.
    return laid.all.filter(n => ids.has(n.id)).map(n => ({ id: n.id, type: 'okf', position: { x: n.x - n.r, y: n.y - n.r }, width: 2 * n.r, height: 2 * n.r,
      draggable: false, data: { label: n.label, sub: n.sub, kind: n.kind, selected: selected === n.id,
        neighbor: !!near && near.has(n.id) && selected !== n.id, dimmed: !!near && !near.has(n.id) } }))
  }, [laid, visibleKey, selected, near])
  const edges = useMemo<Edge[]>(() => {
    const ids = new Set(visibleKey.split(','))
    return laid.edges.flatMap((e, i) => {
      if (!ids.has(e.source) || !ids.has(e.target)) return []
      const lit = !!selected && (e.source === selected || e.target === selected)
      return [{ id: `e${i}`, source: e.source, target: e.target, label: e.label, type: 'straight',
        className: `okf-edge ${lit ? 'edge-highlight' : selected && near ? 'edge-dimmed' : ''}` }]
    })
  }, [laid, visibleKey, selected, near])
  const onNodeClick = useCallback((_: unknown, n: Node) => {
    setSelected(x => x === n.id ? null : n.id)
    setRelQ('')
  }, [])
  const onPaneClick = useCallback(() => setSelected(null), [])
  const liveNode = live?.nodes.find(n => n.id === selected)
  const focus = liveNode?.label

  let canvas
  if (!s.runId) canvas = <Empty icon="bolt" title="No knowledge graph yet" action={<button className="btn btn-primary" onClick={() => goScreen('1.1')}><Icon name="upload" />Upload documents</button>}>The knowledge build starts when verified documents are sent with Start in Upload; the graph appears here when it finishes.</Empty>
  else if (chunk === 'running' || chunk === 'queued') canvas = <Empty icon="clock" title="Chunking and embedding documents…"><Icon name="spinner" spin size={12} /> {s.counts.documents} documents are being chunked.</Empty>
  else if (chunk === 'failed') canvas = <ErrorState title="The knowledge build failed" action={ragPipeline.canStart() ? <button className="btn btn-primary" onClick={() => ragPipeline.retry('chunk')}><Icon name="refresh" />Retry</button> : undefined}>{s.error ?? 'No documents reached the graph.'}</ErrorState>
  else if (!laid.all.length) canvas = <Empty icon="info" title="The graph is empty">The build finished but found no entities in the documents.</Empty>
  else canvas = (
    <div className="graph-box" ref={boxRef}>
      {viewport && <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} viewport={viewport} colorMode={theme} {...LOCKED}
        onNodeClick={onNodeClick} onPaneClick={onPaneClick} aria-label="Knowledge graph">
        <Background gap={24} size={1} />
      </ReactFlow>}
    </div>
  )

  const rels = relations(s)
  // Relations match on the entity name, as the labels are.
  const key = liveNode?.label
  const mine = key ? rels.filter(r => r.from.includes(key) || key.includes(r.from) || r.to.includes(key) || key.includes(r.to)) : []
  const shownRels = relQ ? mine.filter(r => `${r.from} ${r.label} ${r.to}`.toLowerCase().includes(relQ.toLowerCase())) : mine
  const kindOf = liveNode?.type ?? ''
  return (
    <section className="rg" aria-labelledby="center-title">
      <h2 id="center-title" className="sr-only" tabIndex={-1}>Graph explorer</h2>
      <div className="rg-top">
        <div className="class-chips" role="group" aria-label="Filter by entity type">
          {classCounts.map(([c, n]) => (
            <button key={c} className={`class-chip c-${c}`} aria-pressed={!hidden.has(c)}
              onClick={() => setHidden(h => { const x = new Set(h); if (x.has(c)) x.delete(c); else x.add(c); return x })}>
              {hidden.has(c) ? <Icon name="minus" size={10} /> : <Icon name="check" size={10} />}{c} {n}
            </button>
          ))}
        </div>
        <span className="rg-icons">
          <button className="icon-btn sm" aria-label="Graph details" title="Graph details" onClick={() => setDlg('info')}><Icon name="info" size={14} /></button>
          <button className="icon-btn sm" aria-label="All relations" title="All relations" onClick={() => setDlg('list')}><Icon name="menu" size={14} /></button>
        </span>
      </div>
      <div className="rg-canvas">
        {canvas}
        {selected && focus && (
          <aside className="rg-insp" aria-label={`${focus} details`}>
            <header><strong>{focus}</strong><button className="icon-btn sm" aria-label="Close" onClick={() => setSelected(null)}><Icon name="x" size={12} /></button></header>
            <span className="muted xs">{kindOf} · {mine.length} relation{mine.length === 1 ? '' : 's'}</span>
            <label className="sr-only" htmlFor="rel-q">Search relations</label>
            <input id="rel-q" className="rg-search" placeholder="Search relations" value={relQ} onChange={e => setRelQ(e.target.value)} />
            <ul className="rg-rels">
              {shownRels.slice(0, 12).map((r, i) => <li key={i}>{key && (r.from.includes(key) || key.includes(r.from)) ? r.to : r.from} <span className="muted">→ {r.label.replace(/_/g, ' ')}</span></li>)}
              {!shownRels.length && <li className="muted">{graph === 'done' ? 'No relations match.' : 'Relations appear after the graph build.'}</li>}
            </ul>
          </aside>
        )}
        <div className="rg-zoom">
          <button className="icon-btn sm" aria-label="Zoom in" title="Zoom in" onClick={() => setZoom(z => Math.min(2.5, +(z * 1.2).toFixed(2)))}><Icon name="plus" size={13} /></button>
          <button className="icon-btn sm" aria-label="Zoom out" title="Zoom out" onClick={() => setZoom(z => Math.max(0.5, +(z / 1.2).toFixed(2)))}><Icon name="minus" size={13} /></button>
          <button className="icon-btn sm" aria-label="Fit" title="Fit" onClick={() => setZoom(1)}><Icon name="expand" size={13} /></button>
          <span className="rg-count xs">{rels.length} relations · {nodes.length} nodes</span>
        </div>
      </div>
      <Dialog open={dlg === 'info'} title="Graph details" onClose={() => setDlg(null)} className="dialog wide" footer={<button className="btn btn-secondary" onClick={() => setDlg(null)}>Close</button>}>
        <dl className="kv"><div><dt>Knowledge version</dt><dd className="mono">{s.version ?? '—'}</dd></div><div><dt>Nodes / triples</dt><dd>{nodes.length} / {edges.length}</dd></div></dl>
        <PipelineCard />
      </Dialog>
      <Dialog open={dlg === 'list'} title="Entity relations" onClose={() => setDlg(null)} className="dialog wide" footer={<button className="btn btn-secondary" onClick={() => setDlg(null)}>Close</button>}>
        <RelationsCard focus={focus} onSelectEntity={name => { const match = live?.nodes.find(n => n.label === name || name.includes(n.label)); if (match) { setSelected(match.id); setDlg(null) } }} />
      </Dialog>
    </section>
  )
})

// ---- knowledge bundle ----------------------------------------------------------------
/** The version's OKF bundle, built and stored by the backend: the card only offers its download. */
export function OkfBundleCard() {
  const s = useRag(x => x)
  const download = ragActions().downloadBundle
  return (
    <section className="rag-card" aria-labelledby="okf-title">
      <header className="rag-card-head">
        <h3 id="okf-title">Knowledge bundle</h3>
        <span className="muted xs">Open Knowledge Format · {s.counts.documents} documents · {s.counts.entities} entities</span>
        {s.status === 'READY' ? <StatusPill status="pass" text="Ready" /> : <StatusPill status="fail" text={s.status ?? 'Not built'} />}
      </header>
      {download && s.status === 'READY'
        ? <div className="okf-tiers"><button className="btn btn-secondary sm" onClick={() => void download()}><Icon name="db" />Download bundle</button></div>
        : <p className="muted sm">The bundle can be downloaded once a knowledge version is ready.</p>}
    </section>
  )
}
