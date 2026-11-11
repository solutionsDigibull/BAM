import type { Seg } from '../lib/sowDiff'

export function DiffText({ segs }: { segs: Seg[] }) {
  return <>{segs.map((s, i) => s.kind === 'same' ? <span key={i}>{s.text}</span>
    : s.kind === 'removed' ? <del key={i} className="d-removed" title="Removed">{s.text}</del>
    : s.kind === 'added' ? <ins key={i} className="d-added" title="Added">{s.text}</ins>
    : <mark key={i} className="d-modified" title="Modified">{s.text}</mark>)}</>
}
