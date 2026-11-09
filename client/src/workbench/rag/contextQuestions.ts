// Context quality, Questions only (15 Oct 2026, BAM Context quality questions-only wireframes). One store for the
// Results & edits list AND the Enterprise Assistant thread on that screen, so they never disagree. Same storage key
// and row shape as before (`bam-preflight-checks`, PreflightCheck): SOW generation reads the passed rows.
// A question passes only when its answer cites a source in the graph. Sufficiency = passed / total, gate 80%.
import { useSyncExternalStore } from 'react'
import type { PreflightCheck } from './CogneePreflightCockpit'
import { MASTER_SUMMARIES } from './CogneePreflightCockpit'
import { NODES } from './mockData.ts'

export const GATE = 80
/** Why one question failed, in user-facing words (Verify's failed-answer detail). `master` names the master to fix
 *  (a key of MASTER_LINKS in steps/Global.tsx), or none when nothing in the masters is behind the answer. */
export interface Failure { reason: string; expected: string; found: string; source: string; master?: string; record?: string }
export type Row = PreflightCheck & { reason?: string; source?: string; failure?: Failure }

const sample = (id: string, domain: PreflightCheck['domain'], question: string): Row =>
  ({ id, category: 'Sample', domain, question, status: 'pending', answer: '', citations: [], confidence: 0 })
export const SAMPLES: Row[] = [
  sample('cq-1', 'rfq', 'Which parts use 17-4PH and need EN 10204 3.1?'),
  sample('cq-2', 'rfq', 'Which finishes apply to MPW-4421?'),
  sample('cq-3', 'rfq', 'What changed in Rev C compared to Rev B?'),
  sample('cq-4', 'rfq', 'What are the payment terms and delivery date?'),
  sample('cq-5', 'org', 'Which tenant certifications apply?'),
  sample('cq-6', 'rules', 'What machine hour rates and scrap factors apply?'),
]
export const isAsked = (r: PreflightCheck) => r.domain === 'custom'

// ---- the one question-answer call (swap the body for the Cognee search API; keep the result shape) ------------
export interface GraphAnswer { answer: string; source?: string; s3?: string; reason?: string; failure?: Failure }
const rateRule = MASTER_SUMMARIES.rules.find(r => /scrap/i.test(r.details)) ?? MASTER_SUMMARIES.rules[0]
const NO_SOURCE: Failure = {
  reason: 'Nothing in the context matches this question, so there was nothing to answer from.',
  expected: 'An answer that names its source document or master record.',
  found: 'No matching source, so the answer is empty.',
  source: 'Context from the uploaded documents',
}
const machining = MASTER_SUMMARIES.org.find(o => o.code === 'ORG-CORE')!
const SAMPLE_ANSWERS: Record<string, () => GraphAnswer> = {
  'cq-1': () => ({ answer: 'MPW-4422 housing (BOM line 2) is the only 17-4PH part; it needs EN 10204 3.1 certificates.', source: 'BOM_RevC.xlsx · line 2' }),
  'cq-2': () => ({ answer: 'MPW-4421 is 6061-T6 with MIL-A-8625 Type II clear anodize.', source: 'BOM_RevC.xlsx · 6061-T6' }),
  'cq-3': () => ({ answer: 'Rev C replaces Rev B; line 2 quantity was increased.', source: 'email-thread.eml' }),
  'cq-4': () => ({ answer: 'Net 45 terms; quote due 2026-10-20, lead time 4 weeks ARO.', source: 'RFQ_Package_Meridian.pdf' }),
  'cq-5': () => ({ answer: `${machining.code} (${machining.name}): ${machining.certs}.`, source: `Org master · ${machining.code}` }),
  // Answered from the Rules master's line rates and scrap / waste factors (Rule #1); every sample passes (29 Oct 2026, per user).
  'cq-6': () => ({ answer: `${rateRule.rule} (${rateRule.domain}): ${rateRule.details}`, source: `Rules master · ${rateRule.rule}` }),
}
const WORDS = (s: string) => s.toLowerCase().match(/[a-z0-9-]{3,}/g) ?? []
const STOP = new Set(['which', 'what', 'does', 'the', 'and', 'are', 'for', 'apply', 'need', 'needs', 'have', 'with', 'this', 'that', 'there', 'from', 'specified'])
/** Mock graph search over the EXAMPLE graph facts (node facts + their source objects). */
function searchGraph(question: string): GraphAnswer {
  const terms = WORDS(question).filter(w => !STOP.has(w))
  const ids = terms.filter(t => /\d/.test(t)) // part numbers, revisions, specs: a node must contain every one
  let best = { n: 0, node: NODES[0] }
  for (const node of NODES) {
    const hay = `${node.label} ${node.sub ?? ''} ${node.facts} ${node.relations}`.toLowerCase()
    if (!ids.every(t => hay.includes(t))) continue
    const n = terms.filter(t => hay.includes(t)).length
    if (n > best.n) best = { n, node }
  }
  if (!best.n) return { answer: 'No source found for this in the graph.', reason: 'No source in the graph', failure: NO_SOURCE }
  return { answer: `${best.node.label}: ${best.node.facts.replace(/"/g, '')}.`, source: best.node.sources[0] }
}
export async function askGraph(question: string, id?: string): Promise<GraphAnswer> {
  await new Promise(r => setTimeout(r, 900))
  return (id && SAMPLE_ANSWERS[id]?.()) || searchGraph(question)
}

// ---- store -------------------------------------------------------------------------------------------------------
const KEY = 'bam-preflight-checks', BASIS = 'bam-preflight-basis'
/** A saved failed sample whose fixture now answers with a source (cq-6, 29 Oct 2026) loads as passed, not stale-failed. */
const upgrade = (x: Row): Row => {
  const a = x.status === 'flagged' && !isAsked(x) ? SAMPLE_ANSWERS[x.id]?.() : undefined
  return a?.source ? { ...x, status: 'passed', answer: a.answer, source: a.source, reason: undefined, failure: undefined, citations: [{ label: a.source, s3: '' }], confidence: 1 } : x
}
const load = (): Row[] => {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Row[] | null
    return v?.some(x => x.id === 'cq-1') ? v.map(x => upgrade(x.status === 'running' ? { ...x, status: 'pending' } : x)) : SAMPLES
  } catch { return SAMPLES }
}
let rows: Row[] = load()
/** `order`: the ids the last run tested, in order (the assistant thread shows exactly those). */
let state: { running: boolean; phase: 'idle' | 'running' | 'done'; order: string[] } = { running: false, phase: 'idle', order: [] }
const subs = new Set<() => void>()
const emit = () => { try { localStorage.setItem(KEY, JSON.stringify(rows)) } catch { /* storage blocked */ } subs.forEach(f => f()) }
const set = (f: (r: Row[]) => Row[]) => { rows = f(rows); emit() }
const sub = (cb: () => void) => { subs.add(cb); return () => { subs.delete(cb) } }
export const useChecks = () => useSyncExternalStore(sub, () => rows)
export const useRunState = () => useSyncExternalStore(sub, () => state)
export const sufficiency = (c: PreflightCheck[]) => c.length ? Math.round(c.filter(x => x.status === 'passed').length / c.length * 100) : 0
if (rows.some(r => !isAsked(r) && r.status !== 'pending')) state = { running: false, phase: 'done', order: rows.filter(r => !isAsked(r)).map(r => r.id) }

async function test(id: string, retried = false) {
  set(r => r.map(x => x.id === id ? { ...x, status: 'running' } : x))
  const q = rows.find(x => x.id === id)!
  const a = retried ? await retriedAnswer(q) : await askGraph(q.question, isAsked(q) ? undefined : id).catch(() => ({ answer: 'The graph did not answer.', reason: 'No answer from the graph', failure: { ...NO_SOURCE, reason: 'The context did not answer this question (the request failed).', found: 'No answer was returned.' } } as GraphAnswer))
  const ok = !!a.source
  set(r => r.map(x => x.id === id ? { ...x, status: ok ? 'passed' : 'flagged', answer: a.answer, reason: ok ? undefined : a.reason,
    failure: ok ? undefined : a.failure ?? { ...NO_SOURCE, reason: a.reason ?? NO_SOURCE.reason }, source: a.source, citations: a.source ? [{ label: a.source, s3: a.s3 ?? '' }] : [], confidence: ok ? 1 : 0 } : x))
  return rows.find(x => x.id === id)!
}

/** Runs the 6 samples one at a time; a rerun re-tests the failed rows first, then any still untested. */
export async function runSamples() {
  if (state.running) return
  const failed = rows.filter(r => r.status === 'flagged')
  const order = failed.length ? [...failed, ...rows.filter(r => r.status === 'pending')].map(r => r.id) : rows.filter(r => !isAsked(r)).map(r => r.id)
  if (!failed.length) set(r => r.map(x => order.includes(x.id) ? { ...x, status: 'pending', answer: '', source: undefined, reason: undefined, failure: undefined } : x))
  state = { running: true, phase: 'running', order }; emit()
  for (const id of order) await test(id)
  state = { ...state, running: false, phase: 'done' }; emit()
}

// ponytail: demo stand-in. The Masters cannot be edited yet, so a Retry (after the FDE fixed the data) is treated as
// answered from the master the failure named, and Approve unlocks. Replace with askGraph once masters are editable.
async function retriedAnswer(q: Row): Promise<GraphAnswer> {
  await new Promise(r => setTimeout(r, 900))
  const f = q.failure
  return { answer: `Answered after the fix${f?.record ? `: ${f.record}` : ''}.`, source: f?.master ? `${f.master} master · ${f.record ?? 'updated record'}` : 'Context after the fix' }
}
/** One failed question asked again (Verify's failed-answer Retry). Returns the row as it now stands. */
export async function retryQuestion(id: string) {
  if (state.running || !rows.some(x => x.id === id)) return rows.find(x => x.id === id)
  return test(id, true)
}

// The failed answer open in Verify's detail (replaces the list); shared so the footer's "Review issue" can open it.
let open: string | null = null
const openSubs = new Set<() => void>()
export const openFailure = (id: string | null) => { open = id; openSubs.forEach(f => f()) }
export const useOpenFailure = () => useSyncExternalStore(cb => { openSubs.add(cb); return () => { openSubs.delete(cb) } }, () => open)

/** Asked rows can be removed (a question with no possible source would otherwise hold the score down for good). */
export const removeAsked = (id: string) => { if (!state.running) set(r => r.filter(x => x.id !== id || !isAsked(x))) }

/** A question typed or dictated in the assistant: appended to the same list, counts toward the same score. */
export async function askQuestion(question: string) {
  const id = `ask-${Date.now()}`
  set(r => [...r, { id, category: 'Asked', domain: 'custom', question, status: 'pending', answer: '', citations: [], confidence: 0 }])
  return test(id)
}

/** Upstream changed (new RAG run or a schema edit): every row back to Untested. */
export function syncBasis(basis: string) {
  let prev: string | null = null
  try { prev = localStorage.getItem(BASIS); localStorage.setItem(BASIS, basis) } catch { /* storage blocked */ }
  if (prev === null || prev === basis || state.running) return
  state = { running: false, phase: 'idle', order: [] }
  openFailure(null)
  set(r => r.map(x => ({ ...x, status: 'pending', answer: '', source: undefined, reason: undefined, failure: undefined, citations: [] })))
}
