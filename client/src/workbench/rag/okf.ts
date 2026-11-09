// Google Open Knowledge Format (OKF) v0.2 bundle for the RAG context (stage 2). Pure, so it is unit tested.
// Spec: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md (v0.2)
// The bundle is a directory of markdown concepts with YAML frontmatter: `type` is required; v0.2 adds
// provenance (`sources`), trust (`generated`, `verified` -> trust tier) and lifecycle (`status`, `stale_after`).
// It is what we hand to Cognee (`/api/v1/add`) and what a user can download as a tarball.
import type { UploadRecord } from '../api/types.ts'
import { EDGES, NODES, SCHEMAS } from './mockData.ts'
import type { RagState } from './ragPipeline.ts'

export const OKF_VERSION = '0.2'
export const PRODUCER = 'bam-rag/1.0'
export interface OkfFile { path: string; content: string }
export type TrustTier = 'unverified' | 'machine-confirmed' | 'human-reviewed'

type Y = string | number | boolean | undefined | Y[] | { [k: string]: Y }
const scalar = (v: string | number | boolean) =>
  typeof v !== 'string' ? String(v) : /^[\w./@:+-]+$/.test(v) && !/^(true|false|null|yes|no|\d.*)$/i.test(v) ? v : JSON.stringify(v)
const flow = (v: Y): string =>
  Array.isArray(v) ? `[${v.map(flow).join(', ')}]`
    : typeof v === 'object' && v ? `{ ${Object.entries(v).filter(([, x]) => x !== undefined).map(([k, x]) => `${k}: ${flow(x)}`).join(', ')} }`
    : scalar(v as string)
/** Minimal YAML emitter for frontmatter: block keys, flow-style lists/maps, lists of maps as block items. */
function yaml(obj: Record<string, Y>): string {
  return Object.entries(obj).filter(([, v]) => v !== undefined).map(([k, v]) =>
    Array.isArray(v) && v.some(x => typeof x === 'object' && x && !Array.isArray(x))
      ? `${k}:\n${v.map(x => `  - ${flow(x).replace(/^\{ | \}$/g, '').replace(/, (?=\w+: )/g, '\n    ')}`).join('\n')}`
      : `${k}: ${flow(v)}`).join('\n')
}
const doc = (fm: Record<string, Y>, body: string) => `---\n${yaml(fm)}\n---\n\n${body.trim()}\n`
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'concept'
const addDays = (iso: string, d: number) => new Date(new Date(iso).getTime() + d * 864e5).toISOString().replace(/\.\d+Z$/, 'Z')
const z = (iso: string) => iso.replace(/\.\d+Z$/, 'Z')

/** Builds the bundle from what stage 1 released and what stage 2 produced. `reviewer` signs human verification (2.2 hand-off). */
export function buildOkfBundle(uploads: UploadRecord[], rag: Pick<RagState, 'runId' | 'lastSyncAt' | 'handoffs' | 'drafts' | 'steps' | 'graphData' | 'datasetId'>, now: string, reviewer = 'human:fde-reviewer'): OkfFile[] {
  const at = z(rag.lastSyncAt ?? now)
  const cognee = `cognee/${rag.runId ?? 'pending'}`
  const files: OkfFile[] = []
  const released = uploads.filter(u => u.released)

  // Sources: every released (virus-scanned clean) upload. Machine-confirmed by the BuLL*Sav scan (Digibull Security AntiVirus Engine).
  for (const u of released) files.push({ path: `sources/${slug(u.name)}.md`, content: doc({
    type: 'Source Document', title: u.name, description: `Discovery document released by BuLL*Sav, Digibull Security AntiVirus Engine (${(u.size / 1048576).toFixed(1)} MB).`,
    resource: u.objectKey, tags: ['discovery', 'stage-1'],
    generated: { by: 'bam-intake/1.0', at: z(u.uploadedAt) },
    verified: u.scanEndedAt ? [{ by: 'process:bullsav', at: z(u.scanEndedAt) }] : undefined,
    status: 'stable',
  }, `# Source\n\nStored at \`${u.objectKey}\`${u.parts > 1 ? `, uploaded in ${u.parts} chunks` : ''}. Released after a clean BuLL*Sav verdict.`) })

  // Entities: graph nodes extracted by Cognee, with per-claim attribution to their source documents.
  const extracted = rag.steps.find(s => s.key === 'graph')?.status === 'done'
  // Live Cognee graph: one concept per entity, its relations as links between concepts (provenance: the Cognee dataset).
  if (extracted && rag.graphData) {
    const g = rag.graphData
    const path = (n: { label: string; type: string }) => `/entities/${slug(n.type)}/${slug(n.label)}.md`
    const byId = new Map(g.nodes.map(n => [n.id, n]))
    for (const n of g.nodes) {
      const out = g.edges.filter(e => e.source === n.id).map(e => ({ e, o: byId.get(e.target) })).filter(x => x.o)
      const inc = g.edges.filter(e => e.target === n.id).map(e => ({ e, o: byId.get(e.source) })).filter(x => x.o)
      files.push({ path: path(n).slice(1), content: doc({
        type: n.type, title: n.label, description: `${n.type} in the context knowledge graph: ${out.length + inc.length} relations.`,
        tags: [slug(n.type), 'cognee'],
        sources: [{ id: 'cognee', resource: `cognee://datasets/${rag.datasetId ?? 'bam-rfq'}`, title: 'Context knowledge graph' }],
        generated: { by: cognee, at }, status: 'draft', stale_after: addDays(at, 90),
      }, `# Relations\n\n${[...out.map(({ e, o }) => `* ${e.label}: [${o!.label}](${path(o!)})`), ...inc.map(({ e, o }) => `* ${e.label} (from): [${o!.label}](${path(o!)})`)].join('\n') || '* none'}\n\nFrom the context graph build.[^cognee]\n\n[^cognee]: Context knowledge graph`) })
    }
  } else if (extracted) for (const n of NODES) {
    const rel = EDGES.filter(e => e.source === n.id || e.target === n.id)
    const ids = n.sources.map(s => slug(s))
    files.push({ path: `entities/${slug(n.okfClass)}/${slug(n.label)}.md`, content: doc({
      type: n.okfClass, title: n.label + (n.sub ?? ''), description: `${n.okfClass} extracted by the Context Engine: ${n.relations}.`,
      tags: [slug(n.okfClass), 'extracted'],
      sources: n.sources.map(s => ({ id: slug(s), resource: `/sources/${slug(s)}.md`, title: s })),
      generated: { by: cognee, at }, status: 'draft', stale_after: addDays(at, 90), confidence: n.confidence,
    }, `# Facts\n\n${n.facts}.${ids.map(i => `[^${i}]`).join('')}\n\n# Relations\n\n${rel.map(e => {
      const other = NODES.find(x => x.id === (e.source === n.id ? e.target : e.source))!
      return `* ${e.label ?? 'related_to'}: [${other.label}](/entities/${slug(other.okfClass)}/${slug(other.label)}.md)`
    }).join('\n') || '* none'}\n\n${n.sources.map(s => `[^${slug(s)}]: ${s}`).join('\n')}`) })
  }

  // Schemas: proposals; a schema sent to the Workbench in 2.2 is human-reviewed and stable.
  if (rag.steps.find(s => s.key === 'schemas')?.status === 'done') for (const s of SCHEMAS) {
    const draft = rag.drafts[s.name]
    const handoff = [...rag.handoffs].reverse().find(h => h.schema === s.name)
    files.push({ path: `schemas/${slug(s.name)}.md`, content: doc({
      type: 'Schema', title: s.name, description: `Fields: ${s.fields}.`, tags: ['schema', slug(s.okfSource.split(' · ')[0])],
      version: draft?.version, maps_to: s.mapsTo,
      sources: [{ id: 'okf-source', resource: s.okfSource }],
      generated: { by: draft?.editedAt ? reviewer : cognee, at: z(draft?.editedAt ?? at) },
      verified: handoff ? [{ by: reviewer, at: z(handoff.at) }] : undefined,
      status: handoff ? 'stable' : 'draft', stale_after: addDays(at, 90),
    }, `# Schema\n\n\`\`\`python\n${(draft?.code ?? s.code).trim()}\n\`\`\`\n\nDerived from ${s.okfSource}.[^okf-source]\n\n[^okf-source]: ${s.okfSource}`) })
  }

  const section = (title: string, dir: string) => {
    const list = files.filter(f => f.path.startsWith(dir))
    return list.length ? `# ${title}\n\n${list.map(f => `* [${fmTitle(f.content)}](${f.path}) - ${fmField(f.content, 'description') ?? ''}`).join('\n')}\n` : ''
  }
  files.unshift({ path: 'index.md', content: `---\nokf_version: "${OKF_VERSION}"\n---\n\n${[section('Sources', 'sources/'), section('Entities', 'entities/'), section('Schemas', 'schemas/')].filter(Boolean).join('\n')}` })
  files.push({ path: 'log.md', content: `# Directory Update Log\n\n## ${at.slice(0, 10)}\n* **Creation**: ${released.length} sources released by BuLL*Sav; run ${rag.runId ?? 'pending'} produced ${files.filter(f => f.path.startsWith('entities/')).length} entities and ${files.filter(f => f.path.startsWith('schemas/')).length} schemas.\n${rag.handoffs.map(h => `* **Update**: ${h.schema} ${h.version} sent to the Workbench (${z(h.at)}).`).join('\n')}\n` })
  return files
}

// ---- reading frontmatter back (conformance + trust) ----------------------------------------------------
const FM = /^---\n([\s\S]*?)\n---(\n|$)/
export const frontmatter = (content: string) => content.match(FM)?.[1]
const fmField = (content: string, key: string) => frontmatter(content)?.match(new RegExp(`^${key}: (.*)$`, 'm'))?.[1]?.replace(/^"|"$/g, '')
const fmTitle = (content: string) => fmField(content, 'title') ?? fmField(content, 'type') ?? 'Untitled'

/** Trust tier from `verified` (§5.3): none -> unverified; only non-human actors -> machine-confirmed; any human: -> human-reviewed. */
export function trustTier(content: string): TrustTier {
  const lines = (frontmatter(content) ?? '').split('\n')
  const i = lines.findIndex(l => l.startsWith('verified:'))
  if (i < 0) return 'unverified'
  let block = lines[i]
  for (let j = i + 1; j < lines.length && /^\s/.test(lines[j]); j++) block += '\n' + lines[j]
  return /by: human:/.test(block) ? 'human-reviewed' : 'machine-confirmed'
}
export const conceptStatus = (content: string) => (fmField(content, 'status') ?? 'stable') as 'draft' | 'stable' | 'deprecated'
export const conceptType = (content: string) => fmField(content, 'type')

/** OKF v0.2 conformance (§11): every non-reserved .md has a frontmatter block with a non-empty `type`; index.md frontmatter only at the root with okf_version. */
export function validateOkf(files: OkfFile[]): string[] {
  const errors: string[] = []
  for (const f of files) {
    const name = f.path.split('/').pop()!
    if (name === 'log.md') { if (!/^# /m.test(f.content)) errors.push(`${f.path}: log needs a heading`); continue }
    if (name === 'index.md') {
      const fm = frontmatter(f.content)
      if (fm !== undefined && (f.path !== 'index.md' || !/^okf_version: /m.test(fm))) errors.push(`${f.path}: only the root index.md may carry frontmatter (okf_version)`)
      continue
    }
    const fm = frontmatter(f.content)
    if (fm === undefined) errors.push(`${f.path}: missing YAML frontmatter`)
    else if (!fmField(f.content, 'type')) errors.push(`${f.path}: missing required "type"`)
  }
  return errors
}

// ---- tarball (spec §3: a bundle MAY be distributed as a tarball) ----------------------------------------
/** Minimal POSIX ustar writer: regular files only, paths up to 100 bytes (prefix field for longer ones). */
export function toTar(files: OkfFile[], root = 'okf-bundle', mtime = Math.floor(Date.now() / 1000)): Uint8Array {
  const enc = new TextEncoder()
  const blocks: Uint8Array[] = []
  for (const f of files) {
    const data = enc.encode(f.content)
    const h = new Uint8Array(512)
    const put = (s: string, off: number, len: number) => h.set(enc.encode(s).slice(0, len), off)
    const oct = (n: number, len: number) => n.toString(8).padStart(len - 1, '0') + '\0'
    let name = `${root}/${f.path}`, prefix = ''
    if (enc.encode(name).length > 100) { const cut = name.lastIndexOf('/', 154); prefix = name.slice(0, cut); name = name.slice(cut + 1) }
    put(name, 0, 100); put(oct(0o644, 8), 100, 8); put(oct(0, 8), 108, 8); put(oct(0, 8), 116, 8)
    put(oct(data.length, 12), 124, 12); put(oct(mtime, 12), 136, 12); put('        ', 148, 8); put('0', 156, 1)
    put('ustar\0', 257, 6); put('00', 263, 2); put(prefix, 345, 155)
    put(h.reduce((a, b) => a + b, 0).toString(8).padStart(6, '0') + '\0 ', 148, 8)
    blocks.push(h, data, new Uint8Array((512 - (data.length % 512)) % 512))
  }
  blocks.push(new Uint8Array(1024))
  const out = new Uint8Array(blocks.reduce((n, b) => n + b.length, 0))
  let o = 0
  for (const b of blocks) { out.set(b, o); o += b.length }
  return out
}
