import { useEffect, useMemo, useRef } from 'react'
import { EditorContent, Extension, useEditor, type Editor } from '@tiptap/react'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import type { Node as PMNode } from '@tiptap/pm/model'
import StarterKit from '@tiptap/starter-kit'
import type { Sow } from '../api/types'
import { diffBlocks, editCount, type Block, type DiffCounts } from '../lib/sowDiff'
import { Time } from './ui'

// Keeps `data-passage` ids on SOW paragraphs so skill rows can link back to the passage they came from.
export const Passage = Extension.create({
  name: 'passage',
  addGlobalAttributes: () => [{
    types: ['paragraph', 'heading'],
    attributes: {
      passage: {
        default: null,
        keepOnSplit: false,
        parseHTML: el => el.getAttribute('data-passage'),
        renderHTML: a => (a.passage ? { 'data-passage': a.passage } : {}),
      },
    },
  }],
})

export const sowExtensions = [StarterKit, Passage]

// Read-only rendering through the same schema (sanitises whatever the backend returns).
export function ReadonlySow({ html, highlight }: { html: string; highlight?: string }) {
  const editor = useEditor({ extensions: sowExtensions, content: html, editable: false }, [html])
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const root = box.current
    if (!root || !editor) return
    root.querySelectorAll('.passage-hl').forEach(el => el.classList.remove('passage-hl'))
    if (!highlight) return
    const el = root.querySelector(`[data-passage="${CSS.escape(highlight)}"]`)
    if (!el) return
    el.classList.add('passage-hl')
    // After a dialog opening around it has moved focus (which scrolls), so the passage is what ends up in view.
    const t = setTimeout(() => el.scrollIntoView({ block: 'center', behavior: 'smooth' }), 80)
    return () => clearTimeout(t)
  }, [highlight, editor])
  return <div ref={box} className="sow-doc readonly"><EditorContent editor={editor} aria-label="Approved SOW (read-only)" /></div>
}

/** Document blocks in reading order, for the generated-vs-edited comparison. */
export function htmlBlocks(html: string): Block[] {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return [...doc.body.querySelectorAll('h1, h2, h3, p')].map(el => ({
    tag: el.closest('li') ? 'li' : el.closest('blockquote') ? 'quote' : el.tagName.toLowerCase(),
    text: (el.textContent ?? '').replace(/\s+/g, ' ').trim(),
  })).filter(b => b.text)
}

export const useSowDiff = (generatedHtml: string, html: string) =>
  useMemo(() => diffBlocks(htmlBlocks(generatedHtml), htmlBlocks(html)), [generatedHtml, html])

/** System metadata for the SOW (never editable document text). */
export function SowMeta({ sow, compact }: { sow: Sow; compact?: boolean }) {
  const approved = sow.status === 'approved' && sow.approval
  return (
    <dl className={`sow-meta ${compact ? 'compact' : ''}`} aria-label="SOW metadata">
      <div><dt>SOW ID</dt><dd className="mono">{sow.meta.sowId}</dd></div>
      <div><dt>Client</dt><dd>{sow.meta.client}</dd></div>
      {!compact && <div><dt>Length mode</dt><dd>{sow.meta.lengthMode}</dd></div>}
      <div><dt>Status</dt><dd className={approved ? 'meta-ok' : 'meta-draft'}>{approved ? '✓ APPROVED' : sow.status === 'regenerating' ? 'Regenerating' : 'DRAFT · awaiting review'}</dd></div>
      <div><dt>Approved by</dt><dd>{approved ? sow.approval!.by : '—'}</dd></div>
      <div><dt>Approval timestamp</dt><dd>{approved ? <Time iso={sow.approval!.at} /> : '—'}</dd></div>
      <div><dt>Version</dt><dd className="mono">v{sow.version}{approved ? ' (approved)' : ' (draft)'}</dd></div>
      {!compact && <div><dt>Generated from</dt><dd>{sow.meta.sources} source document{sow.meta.sources === 1 ? '' : 's'}</dd></div>}
    </dl>
  )
}

// ---- Live change highlighting inside the editor (display-only decorations, never saved) ----------

type Base = { blocks: Block[] } | null
const changeKey = new PluginKey<{ base: Base; deco: DecorationSet }>('sowChanges')

/** Collapse whitespace like `htmlBlocks`, keeping a map from normalized offsets back to raw offsets. */
function normalize(raw: string) {
  let text = ''
  const idx: number[] = []
  for (let i = 0; i < raw.length; i++) {
    if (/\s/.test(raw[i]!)) { if (text && !text.endsWith(' ')) { text += ' '; idx.push(i) } }
    else { text += raw[i]; idx.push(i) }
  }
  if (text.endsWith(' ')) { text = text.slice(0, -1); idx.pop() }
  return { text, idx }
}

function textblocks(doc: PMNode) {
  const out: { pos: number; node: PMNode; tag: string }[] = []
  doc.descendants((node, pos, parent) => {
    if (!node.isTextblock) return true
    if (node.textContent.trim()) {
      const tag = node.type.name === 'heading' ? `h${node.attrs.level}` : parent?.type.name === 'listItem' ? 'li' : parent?.type.name === 'blockquote' ? 'quote' : 'p'
      out.push({ pos, node, tag })
    }
    return false
  })
  return out
}

const strikeEl = (text: string, block: boolean) => () => {
  const el = document.createElement(block ? 'div' : 'del')
  el.contentEditable = 'false'
  el.title = 'Removed'
  if (block) { el.className = 'db-removed-block'; const d = document.createElement('del'); d.className = 'd-removed'; d.textContent = text; el.append(d) }
  else { el.className = 'd-removed'; el.textContent = text }
  return el
}

// ponytail: offsets assume text-only blocks (marks are fine); inline atoms such as hard breaks shift highlights slightly.
function buildDecorations(doc: PMNode, base: Block[]): Decoration[] {
  const tbs = textblocks(doc)
  const cur = tbs.map(t => normalize(t.node.textContent))
  const { blocks } = diffBlocks(base, tbs.map((t, i) => ({ tag: t.tag, text: cur[i]!.text })))
  const at = (i: number, off: number) => tbs[i]!.pos + 1 + (off >= cur[i]!.text.length ? tbs[i]!.node.textContent!.length : cur[i]!.idx[off]!)
  const decos: Decoration[] = []
  let k = 0
  for (const b of blocks) {
    if (b.kind === 'removed') {
      decos.push(Decoration.widget(k < tbs.length ? tbs[k]!.pos : doc.content.size, strikeEl(b.segs[0]!.text, true), { side: -1, ignoreSelection: true }))
      continue
    }
    if (b.kind === 'added') {
      const t = tbs[k]
      decos.push(Decoration.node(t!.pos, t!.pos + t!.node.nodeSize, { class: 'db-added', title: 'Added' }))
      if (t!.tag.startsWith('h')) decos.push(Decoration.widget(t!.pos + t!.node.nodeSize - 1, () => {
        const el = document.createElement('span'); el.className = 'new-section'; el.textContent = 'New section'; el.contentEditable = 'false'; return el
      }, { side: 1, ignoreSelection: true }))
    } else if (b.kind === 'changed') {
      let off = 0
      for (const sg of b.segs) {
        if (sg.kind === 'removed') { decos.push(Decoration.widget(at(k, off), strikeEl(sg.text, false), { side: -1, ignoreSelection: true })); continue }
        const end = off + sg.text.length
        if (sg.kind !== 'same') decos.push(Decoration.inline(at(k, off), at(k, end), { class: sg.kind === 'added' ? 'd-added' : 'd-modified', title: sg.kind === 'added' ? 'Added' : 'Modified' }))
        off = end
      }
    }
    k++
  }
  return decos
}

/** Colour-codes changes against a base version as you edit. Set the base with `setChangeBase`. */
export const ChangeHighlight = Extension.create({
  name: 'changeHighlight',
  addProseMirrorPlugins: () => [new Plugin({
    key: changeKey,
    state: {
      init: () => ({ base: null as Base, deco: DecorationSet.empty }),
      apply(tr, value, _old, state) {
        const meta = tr.getMeta(changeKey) as { base: Base } | undefined
        if (!meta && !tr.docChanged) return value
        const base = meta ? meta.base : value.base
        return { base, deco: base ? DecorationSet.create(state.doc, buildDecorations(state.doc, base.blocks)) : DecorationSet.empty }
      },
    },
    props: { decorations: state => changeKey.getState(state)?.deco },
  })],
})

/** Compare the editor against `baseHtml` (null turns highlighting off). Not an edit: no history, no autosave. */
export function setChangeBase(editor: Editor, baseHtml: string | null) {
  editor.view.dispatch(editor.state.tr.setMeta(changeKey, { base: baseHtml === null ? null : { blocks: htmlBlocks(baseHtml) } }).setMeta('addToHistory', false))
}

export function DiffLegend({ from, to, counts }: { from: string; to: string; counts?: DiffCounts }) {
  return (
    <div className="diff-legend">
      <span className="legend-key"><ins className="d-added">+ Added</ins><del className="d-removed">− Removed</del><mark className="d-modified">~ Modified</mark></span>
      <span className="muted xs">Baseline <strong>{from}</strong> → current <strong>{to}</strong></span>
      {counts && <span className={`edits-chip ${editCount(counts) ? 'has' : ''}`} role="status">{editCount(counts)} change{editCount(counts) === 1 ? '' : 's'}: {counts.added} added · {counts.removed} removed · {counts.modified} modified</span>}
    </div>
  )
}

