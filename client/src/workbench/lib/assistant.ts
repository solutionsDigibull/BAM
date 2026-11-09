// Enterprise Assistant replies (the middle pane, rendered with assistant-ui). No language model is connected in this
// build: every answer is read from live workflow state, and the assistant never approves, certifies, hands over or
// deploys anything (those stay explicit workflow actions). Pure, so it is unit tested; swap `reply` for an LLM-backed
// ChatModelAdapter later without changing the pane.
import type { Snapshot } from '../api/types'
import type { Blocker } from './prereq.ts'
import type { Statuses } from '../tracker/trackerConfig.ts'

export interface AssistantFacts {
  screen?: { id: string; label: string; stage: string; locked?: string }
  snap: Snapshot
  /** First unmet prerequisite for the active backend step, if any. */
  blocker?: Blocker
  stages: { label: string; status: string; done: number; total: number }[]
  /** Where the work is right now. */
  active: string
  statuses: Statuses
  /** Ingestion: the saved objective, uploads in flight, intake checks, and whether the next message is the objective. */
  intake?: { objective: string; inFlight: number; checksReady: number; checksTotal: number; capture: boolean
    /** Upload's one file counter (UploadPanes `fileCounts`), so replies show the same numbers as the panes. */
    files?: { total: number; uploaded: number } }
  rag?: { runId?: string; steps: { label: string; status: string }[]; relations?: { from: string; fromType: string; label: string; to: string; toType: string }[]; nodes?: number
    schema?: string; schemas?: number; sent?: number; checks?: { passed: number; total: number }; ready?: { label: string; ok: boolean }[]; unclassified?: number }
}

/** On intake screens a message is a question (answered), an objective (saved) or ambiguous (the assistant asks first).
 *  Questions end with "?" or open with an interrogative; ambiguous: "?" mid-text, a request verb, or under 4 words. */
export type Intent = 'question' | 'objective' | 'ambiguous'
export function intentOf(text: string): Intent {
  const t = text.trim()
  if (!t || t.endsWith('?') || /^(what|how|why|when|where|who|whom|whose|which|can|could|should|would|will|shall|may|might|is|are|am|was|were|do|does|did|has|have|had)\b/i.test(t)) return 'question'
  if (t.includes('?') || /^(check|tell me|show|explain|list|find|help|give me)\b/i.test(t) || t.split(/\s+/).length < 4) return 'ambiguous'
  return 'objective'
}
export const isObjective = (text: string) => intentOf(text) === 'objective'
export const CONFIRM_OBJECTIVE = 'Save this as your intake objective?'

/** "N files added" or, while uploading, "X of N uploaded", from the one Upload file counter. */
function filesLine(f: AssistantFacts) {
  const c = f.intake?.files ?? { total: f.snap.uploads.length + (f.intake?.inFlight ?? 0), uploaded: f.snap.uploads.length }
  return !c.total ? '' : c.uploaded < c.total ? `${c.uploaded} of ${c.total} uploaded` : `${c.total} file${c.total === 1 ? '' : 's'} added`
}

export function objectiveReply(f: AssistantFacts): string {
  const line = filesLine(f)
  const checking = f.snap.uploads.some(x => x.scan === 'scanning' && !x.scanError)
  return `Saved as the intake objective.${line ? ` ${line}${checking ? ' and being checked' : ''}.` : ''}`
}

/** Short, live summary of the current screen (shown above the thread; never stored as a message). */
export function screenNote(f: AssistantFacts): string {
  const { snap } = f
  const u = snap.uploads
  const n = (k: (x: Snapshot['uploads'][number]) => boolean) => u.filter(k).length
  const id = f.screen?.id
  if (f.screen?.locked) return `${f.screen.label} is locked. ${f.screen.locked}`
  // Upload checks (scan, quarantine) run inside Upload: the FDE hears readiness, never scan internals.
  const attention = n(x => x.scan === 'blocked' || !!x.scanError), ready = n(x => x.scan === 'verified' && x.released), checking = n(x => x.scan === 'scanning' && !x.scanError)
  const intake = f.intake
  switch (id) {
    case '1.1': return !filesLine(f)
      ? 'Add your first files with + Add files on the left. What would you like this RFQ pack to help you do?'
      : `${filesLine(f)}${checking || intake?.inFlight ? '; checking uploads' : attention ? `; ${attention} need${attention === 1 ? 's' : ''} attention` : '. Ready files can continue to Standardize'}.${intake?.objective ? '' : ' What is this intake for? Dictate or type the objective.'}`
    case '1.8': return !ready ? 'Standardize files: nothing is ready yet. Add documents in Upload.'
      : 'I keep the source files unchanged. Files with uncertain issues go to manual review; known issues use the local AI fix.'
    case '1.9': return !snap.ingestion ? 'Nothing to parse yet. It starts from Start in Upload.' : 'Standardized files are ready. Parse extracts the text, tables and document structure needed by Context.'
    case '1.4': return snap.ingestion ? 'Review confirms that files are accepted and parsed. This is not SOW approval.' : 'No files handed on yet. Standardize the uploads first.'
    case '1.6': return 'Connectors are optional. Add a source if documents live outside your files.'
    case '2.5': return 'I sync Admin Masters so the Context Engine can extract context. The masters stay unchanged.'
    case '2.1': {
      const st = f.rag?.steps ?? []
      if (!f.rag?.runId) return 'No context run yet. It starts from Start in Upload.'
      if (st.some(x => x.status === 'failed')) return `${st.find(x => x.status === 'failed')!.label} failed. The automated flow is paused here: retry the failed step on this screen.`
      if (st.length && st.every(x => x.status === 'done')) return `Index keeps the current graph nodes screen: ${f.rag.relations?.length ?? 0} relations across ${f.rag.nodes ?? 0} nodes. Select a node to inspect its source and relations.`
      return 'Index runs automatically: building the context graph. Schema follows by itself.'
    }
    case '2.2': {
      const failed = (f.rag?.steps ?? []).find(x => x.status === 'failed')
      if (failed) return `${failed.label} failed. The automated flow is paused here: retry the step on this screen.`
      if (!f.rag?.schemas) return 'Schema runs automatically: drafts are proposed once the graph is built, then sent to the Workbench.'
      return `${f.rag.schemas} schema drafts are ready and sent to the Workbench automatically. ${f.rag.schema} is open. Changes stay linked to their source and make dependent results stale. Say "rename unit_price to price" to edit a field.`
    }
    case '2.3': return 'Test the context with questions in this conversation. Answers keep source links; test results appear together on the right.'
    case '2.4': {
      const pending = (f.rag?.ready ?? []).filter(r => !r.ok)
      const extra = f.rag?.unclassified ? ` ${f.rag.unclassified} unclassified file${f.rag.unclassified === 1 ? '' : 's'} ${f.rag.unclassified === 1 ? 'does' : 'do'} not block the SOW.` : ''
      return pending.length ? `Waiting for: ${pending.map(r => r.label).join(', ')}.${extra}` : `Everything is ready.${extra}`
    }
    case '3.1': return !snap.sow ? 'No SOW yet. It is generated from the context once the graph is built.'
      : snap.sow.status === 'approved' ? `SOW v${snap.sow.approval?.version} is approved. Any edit invalidates the approval.` : `Review and edit SOW v${snap.sow.version} in the right pane. The FDE approves it before the Grid.`
    case '3.2': return 'Review the Agent > Skill rows inline. Mark the grid reviewed, then generate the ISATVON Contract.'
    case '4.2': return 'Template shows manifest.json, linked to the approved Scope and Grid.'
    case '4.3': return 'Translate turns the current manifest and approved inputs into checked code.'
    case '4.8': return 'The sandbox uses the current generated artifact. Running it does not commit to BAM_db.'
    case '4.4': case '4.5': case '4.6': case '4.7': return testsLine(snap)
    default: return f.blocker ? `Next: ${f.blocker.reason}` : `Current work: ${f.active}.`
  }
}

function testsLine(snap: Snapshot): string {
  const t = snap.tests, m = snap.workflow?.manifest, p = snap.persist
  if (!m) return 'No manifest yet. Generate the ISATVON Contract in Grid.'
  if (p.deployed?.hash === m.hash) return `Manifest ${m.version} is live on Staging (not production).`
  if (p.committed?.hash === m.hash) return `Manifest ${m.version} is certified and frozen in BAM_db. Next: deploy it to Staging from Commit.`
  if (!t || t.manifestHash !== m.hash) return `Manifest ${m.version} has no test run yet${t ? ' (earlier results are for another hash)' : ''}.`
  const passed = t.assertions.filter(a => a.status === 'pass').length
  return `Checks for ${m.version}: ${t.promptfoo.status}, ${passed} of ${t.assertions.length} assertions passed${t.reviewedAt ? ', certified' : ''}.`
}

const has = (q: string, ...words: string[]) => words.some(w => q.includes(w))

/** Suggestion chips under the composer: getting started before any pack file, contextual once files are added. */
export const STARTER_CHIPS = ['What should I upload first?', 'Explain the 4 stages', 'What makes a good RFQ pack?', 'Check my pack for gaps']
export const FILE_CHIPS = ['Find gaps in these files', 'Draft supplier questions', 'Extract key dates', 'What should I upload next?']

/** Answers for the suggestion chips (always questions, never saved as the objective). Content questions say plainly
 *  that no language model reads the files yet, and give what the workflow state does know. */
export function chipReply(text: string, f: AssistantFacts): string | undefined {
  const files = filesLine(f)
  const noModel = `No language model is connected yet, so I cannot read the file contents.${files ? ` From the workflow: ${files}; the files panel shows each file's type and confidence.` : ' Add pack files with + Add files on the left first.'}`
  switch (text.trim()) {
    case STARTER_CHIPS[0]: return 'Start with the RFQ document itself, then the drawings or spec sheets, the commercial terms (pricing, Incoterms, delivery) and any quality or certification requirements. Add them with + Add files on the left; PDF, Word, Excel, CSV and images are accepted.'
    case STARTER_CHIPS[1]: return 'Ingestion: upload, standardize and parse the pack files.\nContext: sync the masters and build the context graph, then verify it with questions.\nWorkbench: approve the SOW, review the Agent > Skill grid, then the template and generated code.\nValidation: run the sandbox and checks, certify, then commit to BAM_db and deploy to Staging.'
    case STARTER_CHIPS[2]: return 'A good RFQ pack states the scope and quantities, the submission deadline and delivery windows, drawings or specs for every part, pricing and payment terms, Incoterms, and the quality certificates required. Missing any of these shows up later as a gap.'
    case STARTER_CHIPS[3]: case FILE_CHIPS[0]: case FILE_CHIPS[1]: case FILE_CHIPS[2]: return noModel
    case FILE_CHIPS[3]: return `${files ? `${files}. ` : ''}Check the pack covers the RFQ, drawings or specs, commercial terms and quality requirements; add whatever is missing with + Add files.`
  }
}

/** Files attached in the chat are for questions only: they are never uploaded or ingested. */
export const attachmentReply = (names: string[]) =>
  `${names.join(', ')} ${names.length === 1 ? 'is' : 'are'} attached to this chat only, not added to the pack. No language model is connected yet, so I cannot read ${names.length === 1 ? 'it' : 'them'} to answer. To use a file in the workflow, add it with + Add files on the left.`

/** Answer a question from workflow state only. */
export function reply(question: string, f: AssistantFacts): string {
  const q = question.toLowerCase()
  const { snap } = f
  const about = /^tell me about (.+)$/i.exec(question.trim())
  if (about) {
    const name = about[1].trim()
    const rels = (f.rag?.relations ?? []).filter(r => r.from.includes(name) || name.includes(r.from) || r.to.includes(name) || name.includes(r.to))
    if (!rels.length) return `${name} has no relations in the graph yet.`
    const type = rels.find(r => r.from.includes(name) || name.includes(r.from))?.fromType ?? rels[0].toType
    const others = [...new Set(rels.map(r => (r.from.includes(name) || name.includes(r.from)) ? r.to : r.from))].slice(0, 2)
    return `${name} is a ${type.toLowerCase()} node with ${rels.length} relation${rels.length === 1 ? '' : 's'}, linked to ${others.join(' and ')}.`
  }
  if (has(q, 'approve', 'deploy it', 'commit it', 'certify it', 'hand it over', 'publish', 'release it'))
    return 'I can explain the workflow, but I cannot approve, certify, hand over or deploy anything. Those are explicit actions on their own screens, each with its own checks and confirmation.'
  if (has(q, 'scan', 'virus', 'quarantine', 'threat', 'malware', 'held', 'upload', 'file')) {
    const u = snap.uploads
    if (!u.length) return 'No files uploaded yet.'
    const bad = u.filter(x => x.scan === 'blocked' || x.scanError)
    return `${u.length} files: ${u.filter(x => x.scan === 'verified' && x.released).length} ready, ${u.filter(x => x.scan === 'scanning' && !x.scanError).length} being checked, ${bad.length} need${bad.length === 1 ? 's' : ''} attention${bad.length ? ` (${bad.map(b => b.name).join(', ')}: remove and upload a different copy)` : ''}. Only ready files continue to Standardize.`
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
