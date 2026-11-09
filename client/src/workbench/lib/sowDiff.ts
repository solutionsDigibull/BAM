// Generated-vs-edited comparison for the SOW. Pure functions (no DOM) so they can be unit tested.
// Blocks are aligned first (LCS on tag + text), then changed blocks are compared word by word.

export interface Block { tag: string; text: string }
export type SegKind = 'same' | 'added' | 'removed' | 'modified'
export interface Seg { kind: SegKind; text: string }
export interface DiffBlock { tag: string; kind: 'same' | 'added' | 'removed' | 'changed'; segs: Seg[] }
export interface DiffCounts { added: number; removed: number; modified: number }

type Op<T> = { op: 'eq' | 'del' | 'ins'; a?: T; b?: T }

// ponytail: O(n·m) LCS; fine for SOW-sized documents (hundreds of blocks, short paragraphs).
function lcsOps<T>(a: T[], b: T[], eq: (x: T, y: T) => boolean): Op<T>[] {
  const n = a.length, m = b.length
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = eq(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const ops: Op<T>[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (eq(a[i], b[j])) { ops.push({ op: 'eq', a: a[i], b: b[j] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) ops.push({ op: 'del', a: a[i++] })
    else ops.push({ op: 'ins', b: b[j++] })
  }
  while (i < n) ops.push({ op: 'del', a: a[i++] })
  while (j < m) ops.push({ op: 'ins', b: b[j++] })
  return ops
}

/** Word-level diff of one block. A removal directly followed by an insertion is a modification. */
export function diffWords(oldText: string, newText: string): Seg[] {
  // Tokens are words with their trailing space; compared without it so spacing never splits a phrase.
  const tok = (s: string) => s.match(/\s*\S+\s*/g) ?? []
  const raw: Seg[] = []
  for (const o of lcsOps(tok(oldText), tok(newText), (x, y) => x.trim() === y.trim())) {
    const kind: SegKind = o.op === 'eq' ? 'same' : o.op === 'del' ? 'removed' : 'added'
    const text = (o.op === 'del' ? o.a : o.b)!
    const last = raw[raw.length - 1]
    if (last && last.kind === kind) last.text += text
    else raw.push({ kind, text })
  }
  const segs: Seg[] = []
  for (let i = 0; i < raw.length; i++) {
    const s = raw[i]
    if (s.kind === 'removed' && raw[i + 1]?.kind === 'added') { segs.push(s, { kind: 'modified', text: raw[i + 1].text }); i++ }
    else segs.push(s)
  }
  // Whitespace at the edges of a change belongs to the unchanged text around it. A removed run's
  // whitespace exists only in the old text, so it is dropped: joining the non-removed segments
  // must reproduce the new text exactly (the editor maps highlights by these offsets).
  return segs.flatMap(s => {
    if (s.kind === 'same') return [s]
    const [, lead, core, trail] = s.text.match(/^(\s*)(.*?)(\s*)$/s)!
    if (s.kind === 'removed') return [{ kind: s.kind, text: core }]
    return [{ kind: 'same' as const, text: lead }, { kind: s.kind, text: core }, { kind: 'same' as const, text: trail }]
  }).filter(s => s.text)
}

const wordSet = (t: string) => new Set(t.toLowerCase().match(/[\w-]+/g) ?? [])
function pairable(a: Block, b: Block) {
  if (a.tag !== b.tag) return false
  const x = wordSet(a.text), y = wordSet(b.text)
  const shared = [...x].filter(w => y.has(w)).length
  return shared / Math.max(x.size, y.size, 1) >= 0.3
}

export function diffBlocks(oldBlocks: Block[], newBlocks: Block[]): { blocks: DiffBlock[]; counts: DiffCounts } {
  const counts: DiffCounts = { added: 0, removed: 0, modified: 0 }
  const blocks: DiffBlock[] = []
  const ops = lcsOps(oldBlocks, newBlocks, (x, y) => x.tag === y.tag && x.text === y.text)
  let dels: Block[] = [], ins: Block[] = []
  const flush = () => {
    // Pair a removed and an inserted block only when they are clearly the same block edited
    // (same kind, shared wording); otherwise show a removal and an addition.
    while (dels.length || ins.length) {
      const d = dels[0], n = ins[0]
      if (d && n && pairable(d, n)) {
        const segs = diffWords(d.text, n.text)
        addCounts(counts, countSegs(segs))
        blocks.push({ tag: n.tag, kind: 'changed', segs })
        dels.shift(); ins.shift()
      } else if (d && !ins.some(x => pairable(d, x))) {
        blocks.push({ tag: d.tag, kind: 'removed', segs: [{ kind: 'removed', text: d.text }] }); counts.removed++; dels.shift()
      } else {
        blocks.push({ tag: n!.tag, kind: 'added', segs: [{ kind: 'added', text: n!.text }] }); counts.added++; ins.shift()
      }
    }
  }
  for (const o of ops) {
    if (o.op === 'eq') { flush(); blocks.push({ tag: o.b!.tag, kind: 'same', segs: [{ kind: 'same', text: o.b!.text }] }) }
    else if (o.op === 'del') dels.push(o.a!)
    else ins.push(o.b!)
  }
  flush()
  return { blocks, counts }
}

export const editCount = (c: DiffCounts) => c.added + c.removed + c.modified

/** Edits in one diffed text. A removal that is replaced (removed, then modified) counts once, as a modification. */
export function countSegs(segs: Seg[]): DiffCounts {
  const c: DiffCounts = { added: 0, removed: 0, modified: 0 }
  const real = segs.filter(x => x.kind !== 'same' || x.text.trim())
  real.forEach((x, i) => { if (x.kind !== 'same' && !(x.kind === 'removed' && real[i + 1]?.kind === 'modified')) c[x.kind]++ })
  return c
}
export function addCounts(into: DiffCounts, c: DiffCounts) { into.added += c.added; into.removed += c.removed; into.modified += c.modified; return into }
