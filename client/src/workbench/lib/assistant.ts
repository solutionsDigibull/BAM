// Enterprise Assistant replies (the middle pane, rendered with assistant-ui). No language model is connected: every
// answer is read from the live project snapshot, and the assistant never approves, certifies, hands over or deploys
// anything (those stay explicit workflow actions). Pure, so it is unit tested; swap `reply` for an LLM-backed
// ChatModelAdapter later without changing the pane (decision: #729).
import type { Snapshot } from '../api/types'
import type { Blocker } from './prereq'
import type { Statuses } from '../tracker/trackerConfig'

export interface AssistantFacts {
  screen?: { id: string; label: string; stage: string; locked?: string }
  snap: Snapshot
  /** First unmet prerequisite for the active backend step, if any. */
  blocker?: Blocker
  stages: { label: string; status: string; done: number; total: number }[]
  /** Where the work is right now. */
  active: string
  statuses: Statuses
  /** Upload's one file counter (UploadPanes `fileCounts`), so replies show the same numbers as the panes. */
  files?: { total: number; uploaded: number }
  rag?: { runId?: string; steps: { label: string; status: string }[]; relations?: { from: string; fromType: string; label: string; to: string; toType: string }[]; nodes?: number }
}

/** "N files added" or, while uploading, "X of N uploaded", from the one Upload file counter. */
function filesLine(f: AssistantFacts) {
  const c = f.files ?? { total: f.snap.uploads.length, uploaded: f.snap.uploads.length }
  return !c.total ? '' : c.uploaded < c.total ? `${c.uploaded} of ${c.total} uploaded` : `${c.total} file${c.total === 1 ? '' : 's'} added`
}

/** Short, live summary of the current screen (shown in the empty thread's greeting; never stored as a message). */
export function screenNote(f: AssistantFacts): string {
  const { snap } = f
  const n = (k: (x: Snapshot['uploads'][number]) => boolean) => snap.uploads.filter(k).length
  const id = f.screen?.id
  if (f.screen?.locked) return `${f.screen.label} is locked. ${f.screen.locked}`
  // Upload checks (scan, quarantine) run inside Upload: the FDE hears readiness, never scan internals.
  const attention = n(x => x.scan === 'blocked' || !!x.scanError), checking = n(x => x.scan === 'scanning' && !x.scanError)
  switch (id) {
    case '1.1': return !filesLine(f)
      ? 'Add your first files with + Add files on the left.'
      : `${filesLine(f)}${checking ? '; checking uploads' : attention ? `; ${attention} need${attention === 1 ? 's' : ''} attention` : '. Ready files can continue to Parse'}.`
    case '1.9': return !snap.ingestion ? 'Nothing to parse yet. It starts from Start in Upload.' : 'Parse extracts the text, tables and document structure needed by Context.'
    case '2.5': return 'Context syncs the Admin Masters so the knowledge can be built. The masters stay unchanged.'
    case '2.1': {
      const st = f.rag?.steps ?? []
      if (!f.rag?.runId) return 'No context run yet. It starts from Start in Upload.'
      if (st.some(x => x.status === 'failed')) return `${st.find(x => x.status === 'failed')!.label} failed. The automated flow is paused here: retry the failed step on this screen.`
      if (st.length && st.every(x => x.status === 'done')) return `The context graph has ${f.rag.relations?.length ?? 0} relations across ${f.rag.nodes ?? 0} nodes. Select a node to inspect its source and relations.`
      return 'Index runs automatically: building the context graph.'
    }
    case '3.1': return !snap.sow ? 'No SOW yet. It is generated from the context once the graph is built.'
      : snap.sow.status === 'approved' ? `SOW v${snap.sow.approval?.version} is approved. Any edit invalidates the approval.` : `Review and edit SOW v${snap.sow.version} in the right pane. The FDE approves it before the Grid.`
    case '3.2': return 'Review the Agent > Skill rows inline. Mark the grid reviewed, then generate the ISATVON Contract.'
    case '4.2': return 'Template shows manifest.json, linked to the approved Scope and Grid.'
    case '4.3': return 'Translate turns the current manifest and approved inputs into checked code.'
    case '4.8': return 'The sandbox uses the current generated artifact. Running it does not commit to BAM_db.'
    case '4.4': return testsLine(snap)
    default: return f.blocker ? `Next: ${f.blocker.reason}` : `Current work: ${f.active}.`
  }
}

function testsLine(snap: Snapshot): string {
  const t = snap.tests, m = snap.workflow?.manifest, p = snap.persist
  if (!m) return 'No manifest yet. Generate the ISATVON Contract in Grid.'
  if (p.deployed?.hash === m.hash) return `Manifest ${m.version} is live on Staging (not production).`
  if (p.committed?.hash === m.hash) return `Manifest ${m.version} is certified and frozen in BAM_db.`
  if (!t || t.manifestHash !== m.hash) return `Manifest ${m.version} has no test run yet${t ? ' (earlier results are for another hash)' : ''}.`
  const passed = t.assertions.filter(a => a.status === 'pass').length
  return `Checks for ${m.version}: ${t.promptfoo.status}, ${passed} of ${t.assertions.length} assertions passed${t.reviewedAt ? ', certified' : ''}.`
}

const has = (q: string, ...words: string[]) => words.some(w => q.includes(w))

/** Suggestion chips under the composer: each is a question `reply` answers from the workflow state. */
export const STARTER_CHIPS = ['What is next?', 'Why is this locked?', 'Upload status', 'Workflow progress']

/** Files attached in the chat are for questions only: they are never uploaded or ingested. */
export const attachmentReply = (names: string[]) =>
  `${names.join(', ')} ${names.length === 1 ? 'is' : 'are'} attached to this chat only, not added to the project. No language model is connected yet, so I cannot read ${names.length === 1 ? 'it' : 'them'} to answer. To use a file in the workflow, add it with + Add files in Upload.`

/** Answer a question from workflow state only. */
export function reply(question: string, f: AssistantFacts): string {
  const q = question.toLowerCase()
  const { snap } = f
  const about = /^tell me about (.+)$/i.exec(question.trim())
  if (about) {
    const name = about[1]!.trim()
    const rels = (f.rag?.relations ?? []).filter(r => r.from.includes(name) || name.includes(r.from) || r.to.includes(name) || name.includes(r.to))
    if (!rels.length) return `${name} has no relations in the graph yet.`
    const type = rels.find(r => r.from.includes(name) || name.includes(r.from))?.fromType ?? rels[0]!.toType
    const others = [...new Set(rels.map(r => (r.from.includes(name) || name.includes(r.from)) ? r.to : r.from))].slice(0, 2)
    return `${name} is a ${type.toLowerCase()} node with ${rels.length} relation${rels.length === 1 ? '' : 's'}, linked to ${others.join(' and ')}.`
  }
  if (has(q, 'approve', 'deploy it', 'commit it', 'certify it', 'hand it over', 'publish', 'release it'))
    return 'I can explain the workflow, but I cannot approve, certify, hand over or deploy anything. Those are explicit actions on their own screens, each with its own checks and confirmation.'
  if (has(q, 'scan', 'virus', 'quarantine', 'threat', 'malware', 'held', 'upload', 'file')) {
    const u = snap.uploads
    if (!u.length) return 'No files uploaded yet.'
    const bad = u.filter(x => x.scan === 'blocked' || x.scanError)
    const why = bad.map(x => `${x.name}: ${x.threat ?? x.scanError}`).join('; ')
    return `${u.length} files: ${u.filter(x => x.scan === 'verified' && x.released).length} ready, ${u.filter(x => x.scan === 'scanning' && !x.scanError).length} being checked, ${bad.length} need${bad.length === 1 ? 's' : ''} attention${bad.length ? ` (${why})` : ''}.`
  }
  if (has(q, 'why', 'lock', 'blocked', "can't", 'cannot', 'disabled'))
    return f.screen?.locked ? `${f.screen.label} is locked: ${f.screen.locked}` : f.blocker ? `${f.screen?.label ?? 'This screen'} is open. The next workflow gate: ${f.blocker.reason}` : `Nothing blocks ${f.screen?.label ?? 'this screen'} right now.`
  if (has(q, 'sow', 'statement of work', 'scope')) return screenNote({ ...f, screen: { id: '3.1', label: 'Scope', stage: 'Workbench' } })
  if (has(q, 'test', 'assert', 'promptfoo', 'sandbox', 'staging', 'certif', 'handover', 'deploy')) return testsLine(snap)
  if (has(q, 'next', 'what now', 'what should', 'todo', 'to do'))
    return f.blocker ? `${f.blocker.reason} (${f.blocker.label}.)` : `Continue in ${f.active}.`
  if (has(q, 'status', 'progress', 'where', 'summary', 'summarise', 'summarize', 'overview'))
    return f.stages.map(s => `${s.label}: ${s.status} (${s.done}/${s.total})`).join('\n')
  if (has(q, 'help', 'what can you', 'who are you'))
    return 'Ask me what is next, why a step is locked, or for the status of uploads, the SOW, checks or Staging. I answer from the live workflow state.'
  return 'I answer from the live workflow state: try "What is next?", "Why is this locked?", "Upload status" or "Workflow progress". I cannot answer open questions yet.'
}
