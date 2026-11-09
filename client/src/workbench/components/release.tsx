// Release evidence shared by 3.2, 3.4, 4.2 and 4.4: certification checklist, version history and the
// draft-versus-certified manifest comparison. All read-only; decisions live in the centre panes.
import { useMemo } from 'react'
import type { Snapshot, VersionEntry } from '../api/types'
import { certification, certifiedBaseline, type CertState } from '../lib/certification'
import { diffBlocks, type DiffBlock } from '../lib/sowDiff'
import { DEMO_RUN } from '../lib/demo'
import { goScreen } from '../lib/route'
import { Copy, Empty, Icon, StatusPill, Time, shortHash } from './ui'

const PILL: Record<CertState, Parameters<typeof StatusPill>[0]['status']> = {
  pass: 'pass', fail: 'fail', pending: 'pending', stale: 'stale', running: 'running', unavailable: 'unavailable',
}
const PILL_TEXT: Record<CertState, string> = { pass: 'Pass', fail: 'Fail', pending: 'Pending', stale: 'Stale', running: 'Running', unavailable: 'Unavailable' }

export function DemoTag() {
  return DEMO_RUN ? <span className="demo-tag" title="Example result for review; not produced by a live run">simulated</span> : null
}

export function CertChecklist({ snap, compact }: { snap: Snapshot; compact?: boolean }) {
  const c = certification(snap)
  return (
    <div className={`cert ${compact ? 'compact' : ''}`}>
      <div className="cert-head">
        <StatusPill status={c.certified ? 'pass' : 'pending'} text={c.certified ? 'Certified' : 'Not certified'} />
        {c.manifest ? <span className="muted xs">for manifest <span className="mono">{c.manifest.version}</span> · <Copy value={c.manifest.hash} display={shortHash(c.manifest.hash)} label="manifest hash" /></span>
          : <span className="muted xs">No manifest yet</span>}
        {c.certified && <DemoTag />}
      </div>
      <ol className="cert-list">
        {c.items.map(i => (
          <li key={i.key} className={`cert-item cs-${i.state}`}>
            <StatusPill status={PILL[i.state]} text={PILL_TEXT[i.state]} />
            <div className="cert-main">
              <span className="cert-label">{i.label}</span>
              {!compact && <span className="cert-detail">{i.detail}</span>}
              {compact && i.state !== 'pass' && <span className="cert-detail">{i.detail}</span>}
            </div>
            {i.ref && !compact && <Copy value={i.ref} display={i.ref.length > 22 ? `${i.ref.slice(0, 22)}…` : i.ref} label="evidence reference" className="cert-ref" />}
          </li>
        ))}
      </ol>
    </div>
  )
}

const KIND_TEXT: Record<VersionEntry['kind'], string> = { draft: 'Draft', certified: 'Certified', frozen: 'Frozen', deployed: 'Deployed' }
const KIND_PILL: Record<VersionEntry['kind'], Parameters<typeof StatusPill>[0]['status']> = { draft: 'needs_review', certified: 'pass', frozen: 'done', deployed: 'succeeded' }

export function VersionHistory({ snap, limit }: { snap: Snapshot; limit?: number }) {
  const rows = [...snap.versions].reverse().slice(0, limit)
  const current = snap.workflow?.manifest?.hash
  if (!rows.length) return <p className="muted sm">No manifest versions yet. The first draft is written when the Template checks A–C pass.</p>
  return (
    <ol className="vhist">
      {rows.map((v, i) => (
        <li key={i} className={`vh k-${v.kind} ${v.hash === current ? 'is-current' : ''}`}>
          <StatusPill status={KIND_PILL[v.kind]} text={KIND_TEXT[v.kind]} />
          <span className="mono sm">{v.version}</span>
          <Copy value={v.hash} display={v.hash.slice(0, 8)} label="hash" />
          <span className="muted xs vh-note">{v.note}{v.hash === current && ' · current'}{v.kind !== 'draft' && DEMO_RUN && ' · simulated'}</span>
          <Time iso={v.at} />
        </li>
      ))}
    </ol>
  )
}

const lines = (s: string) => s.split('\n').map(text => ({ tag: 'l', text }))

/** Line diff of two texts (read-only), with + / − / ~ markers so colour is not the only cue. */
export function LineDiff({ base, current, context = 2 }: { base: string; current: string; context?: number }) {
  const { blocks, counts } = useMemo(() => diffBlocks(lines(base), lines(current)), [base, current])
  const changed = blocks.map(b => b.kind !== 'same')
  const near = (i: number) => changed.slice(Math.max(0, i - context), i + context + 1).some(Boolean)
  const out: (DiffBlock | 'gap')[] = []
  blocks.forEach((b, i) => { if (b.kind !== 'same' || near(i)) out.push(b); else if (out[out.length - 1] !== 'gap') out.push('gap') })
  return (
    <div className="line-diff">
      <div className="ld-count" role="status">
        <span className="d-added">+{counts.added} added</span><span className="d-removed-txt">−{counts.removed} removed</span><span className="d-modified">~{counts.modified} modified</span>
      </div>
      <pre className="ld-body" tabIndex={0} aria-label="Line-by-line comparison">
        {out.map((b, i) => b === 'gap' ? <span key={i} className="ld-gap">⋯</span>
          : <span key={i} className={`ld-line ld-${b.kind}`}>
              <span className="ld-mark" aria-hidden>{b.kind === 'added' ? '+' : b.kind === 'removed' ? '−' : b.kind === 'changed' ? '~' : ' '}</span>
              <span className="sr-only">{b.kind === 'same' ? '' : b.kind}</span>
              {b.segs.map((s, j) => s.kind === 'same' ? <span key={j}>{s.text}</span>
                : s.kind === 'removed' ? <del key={j} className="d-removed">{s.text}</del>
                : s.kind === 'added' ? <ins key={j} className="d-added">{s.text}</ins>
                : <mark key={j} className="d-modified">{s.text}</mark>)}
            </span>)}
      </pre>
    </div>
  )
}

/** Draft manifest versus the latest certified version (read-only). */
export function ManifestCompare({ snap, link = true }: { snap: Snapshot; link?: boolean }) {
  const m = snap.workflow?.manifest
  const base = certifiedBaseline(snap)
  if (!m) return <Empty icon="file" title="No draft manifest yet">The manifest is written when the Template checks A–C pass. Generate the ISATVON Contract in the Skills grid to start.</Empty>
  const current = JSON.stringify(m, null, 2)
  return (
    <div className="mcompare">
      <p className="mc-head">
        <span>Draft <span className="mono">{m.version}</span> <span className="muted mono xs">{m.hash.slice(0, 8)}</span></span>
        <Icon name="chevron" size={11} />
        {base ? <span>certified <span className="mono">{base.version}</span> <span className="muted mono xs">{base.hash.slice(0, 8)}</span></span> : <span className="muted">no certified version</span>}
      </p>
      {!base ? <p className="muted sm"><strong>No certified version yet.</strong> This is the first build: nothing has passed the sandbox, assertions and review, so there is no release baseline to compare against. Generated data is not certified data.</p>
        : base.hash === m.hash ? <p className="muted sm"><Icon name="check" size={12} /> The draft is identical to the certified version (same hash).</p>
        : <LineDiff base={base.manifest} current={current} />}
      {link && <button className="link sm" onClick={() => goScreen('4.2')}><Icon name="link" size={12} />Open Manifest JSON</button>}
    </div>
  )
}
