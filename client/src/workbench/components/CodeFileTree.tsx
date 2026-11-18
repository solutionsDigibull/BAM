import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { buildFileTree, visibleFileRows } from '../lib/fileTree'
import { Icon } from './ui'

const ROW = 28
export function CodeFileTree({ paths, current, onSelect, status }: {
  paths: string[]; current?: string; onSelect: (path: string) => void; status: (path: string) => string
}) {
  const pathKey = paths.join('\0')
  const tree = useMemo(() => buildFileTree(paths), [pathKey])
  const [collapsed, setCollapsed] = useState(new Set<string>())
  const rows = useMemo(() => visibleFileRows(tree, collapsed), [tree, collapsed])
  const [active, setActive] = useState(current ?? '')
  const [scroll, setScroll] = useState(0), [height, setHeight] = useState(500)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => { const el = box.current; if (!el) return; const o = new ResizeObserver(() => setHeight(el.clientHeight)); o.observe(el); return () => o.disconnect() }, [])
  useEffect(() => {
    if (!current) return
    setActive(current)
    setCollapsed(prev => new Set([...prev].filter(p => !current.startsWith(`${p}/`))))
  }, [current])
  useEffect(() => {
    const index = rows.findIndex(r => r.node.path === active), el = box.current
    if (!el || index < 0) return
    if (index * ROW < el.scrollTop) el.scrollTop = index * ROW
    else if ((index + 1) * ROW > el.scrollTop + el.clientHeight) el.scrollTop = (index + 1) * ROW - el.clientHeight
  }, [active, rows])
  const toggle = (path: string) => setCollapsed(prev => { const next = new Set(prev); next.has(path) ? next.delete(path) : next.add(path); return next })
  const keyDown = (e: KeyboardEvent) => {
    const index = Math.max(0, rows.findIndex(r => r.node.path === active)), row = rows[index]
    if (!row) return
    let next = index
    if (e.key === 'ArrowDown') next = Math.min(rows.length - 1, index + 1)
    else if (e.key === 'ArrowUp') next = Math.max(0, index - 1)
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = rows.length - 1
    else if (e.key === 'ArrowRight' && row.node.folder) { if (collapsed.has(row.node.path)) toggle(row.node.path); else next = Math.min(rows.length - 1, index + 1) }
    else if (e.key === 'ArrowLeft') { if (row.node.folder && !collapsed.has(row.node.path)) toggle(row.node.path); else next = Math.max(0, rows.findIndex(r => r.node.path === row.parent)) }
    else if (e.key === 'Enter' || e.key === ' ') { row.node.folder ? toggle(row.node.path) : onSelect(row.node.path) }
    else return
    e.preventDefault(); setActive(rows[next]!.node.path)
  }
  const start = Math.max(0, Math.floor(scroll / ROW) - 8), end = Math.min(rows.length, Math.ceil((scroll + height) / ROW) + 8)
  return <aside className="code-explorer" aria-label="Generated files">
    <h3>FILES</h3>
    <div ref={box} className="code-tree-scroll" role="tree" aria-label="Generated file tree" tabIndex={0} onKeyDown={keyDown}
      aria-activedescendant={rows.some((r, i) => i >= start && i < end && r.node.path === active) ? `code-file-${rows.findIndex(r => r.node.path === active)}` : undefined}
      onScroll={e => setScroll(e.currentTarget.scrollTop)}>
      <div style={{ height: rows.length * ROW, position: 'relative' }}>
        {rows.slice(start, end).map(({ node, depth }, offset) => {
          const index = start + offset, selected = !node.folder && node.path === current
          return <div key={node.path} id={`code-file-${index}`} role="treeitem" aria-level={depth + 1} aria-selected={selected}
            aria-expanded={node.folder ? !collapsed.has(node.path) : undefined} title={`${node.path}${status(node.path)}`}
            className={`code-tree-row ${selected ? 'selected' : ''} ${active === node.path ? 'active' : ''}`}
            style={{ top: index * ROW, paddingLeft: 8 + depth * 16 }} onClick={() => { setActive(node.path); node.folder ? toggle(node.path) : onSelect(node.path); box.current?.focus() }}>
            {Array.from({ length: depth }, (_, i) => <span key={i} className="code-tree-guide" style={{ left: 15 + i * 16 }} />)}
            <span className={`code-tree-chevron ${node.folder && !collapsed.has(node.path) ? 'open' : ''}`}>{node.folder && <Icon name="chevron" size={11} />}</span>
            {node.folder ? <Icon name="folder" size={13} /> : <span className="code-file-type" aria-hidden>{node.name.split('.').pop()?.slice(0, 3) || 'file'}</span>}
            <span className="code-tree-name">{node.name}</span>{status(node.path) && <span className="code-tree-mark" aria-label={status(node.path)}>•</span>}
          </div>
        })}
      </div>
    </div>
  </aside>
}
