// Upload goal capture (29 Oct 2026, "Upload step & assistant changes" wireframes; dropdowns the same day): "Build an
// AI agent that automates [What] for [Who] in [Where]" expands into an ISATVON draft (right pane) and the supporting
// files its Source data needs. No model is connected: the draft comes from a per-What profile (Quoting = the quotation
// example; a custom "Others" What uses generic wording from its own words), and the
// assistant's edits (approval threshold, margin guardrail) are rule-based too. Pure helpers are unit tested.
// ponytail: rule-based mock; swap expandGoal / applyEdit for the model call when one exists.
import { useSyncExternalStore } from 'react'

export interface Goal { what: string; who: string; where: string }
export const KEYS = ['I', 'S', 'A', 'T', 'V', 'O', 'N'] as const
export type Key = typeof KEYS[number]
export const SECTION: Record<Key, string> = { I: 'Instructions', S: 'Source data', A: 'Automation', T: 'Tech Stack', V: 'Variables', O: 'Outcome', N: 'Next' }
export interface Upload { name: string; hint: string; required: boolean; match: RegExp }
/** Sections hold inner HTML (the output editor keeps formatting); `base` is the generated version ("what changed"). */
export interface Draft { goal: Goal; quote: boolean; title: string; subtitle: string; sections: Record<Key, string>; base: Record<Key, string>; uploads: Upload[]
  /** What the approval threshold applies to, plural ("Quotes", "Work orders"). */
  item: string
  /** Changes on every expansion and assistant edit (the output editor reloads then; typed edits never change it). */
  rev: number }

// TODO(backend): the option lists and profiles below are local constants; load them from the backend when a
// goal-catalogue contract exists. "Others" sits last in every menu and lets the FDE type their own value.
export const OTHERS = 'Others'
export const WHAT_OPTIONS = ['Quoting', 'RFQ', 'Quality engineering', 'Assets management', 'GTM workflow', 'Lead generation', OTHERS]
export const WHO_OPTIONS = ['Sales managers at SMB manufacturers', 'Quality engineers and QA teams', 'Plant and maintenance managers', 'Marketing and growth teams', 'Procurement and sourcing teams', 'Operations managers', OTHERS]
export const WHERE_OPTIONS = ['India', 'Southeast Asia', 'Middle East', 'Europe', 'North America', 'Global', OTHERS]
export const EXAMPLE: Goal = { what: 'Quoting', who: 'Sales managers at SMB manufacturers', where: 'India' }
/** "Sales managers at SMB…" → "sales managers at SMB…" (an acronym like "RFQ" or "GTM workflow" keeps its case). */
const lowerFirst = (s: string) => /^[A-Z][a-z]/.test(s.trim()) ? s.trim().charAt(0).toLowerCase() + s.trim().slice(1) : s.trim()
export const sentence = (g: Goal) => `Build an AI agent that automates ${lowerFirst(g.what)} for ${lowerFirst(g.who)} in ${g.where.trim()}`

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
export const plain = (html: string) => html.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').trim()
const cap = (s: string) => s.replace(/\b\w/g, c => c.toUpperCase())
/** "Sales managers at SMB manufacturers" → "the sales manager". */
const owner = (who: string) => `the ${lowerFirst((who.split(/\s+(?:at|in|of|for|and)\s+/i)[0] || 'team').trim()).replace(/s$/i, '')}`

/** One generation profile per What option; a custom ("Others") What uses the generic one worded from its own value.
 *  `ready` is what gets reviewed ("a quote"); `item` is what the sign-off threshold applies to. */
interface Profile { title: string; flow: string; item: string; ready: string; I: string; S: string; A: string; V: string; O: string; uploads: Upload[] }
const req = (name: string, hint: string, match: RegExp): Upload => ({ name, hint, required: true, match })
const opt = (name: string, hint: string, match: RegExp): Upload => ({ name, hint, required: false, match })
const PROFILES: Record<string, Profile> = {
  Quoting: { title: 'Quote Automation Agent', flow: 'Quote Automation: enquiry → cost estimate → quote → approval → delivery', item: 'Quotes', ready: 'a quote',
    I: 'Prepare quotations from enquiries and route them for human approval before delivery.',
    S: 'Enquiries, pricing rules and product data. Supporting files to be uploaded next.',
    A: 'Enquiry → cost estimate → draft quote → approval → delivery. Check required details before moving on.',
    V: 'Currency, pricing, margins, approval limits and retry bounds: to be confirmed.',
    O: 'Reviewed quote with cost breakdown, approval status and an audit trail.',
    uploads: [req('Enquiry samples', 'RFQ emails or spreadsheets', /enquir|inquir|rfq|request/i), req('Pricing rules', 'Rates, discounts and margins', /pric|rate|discount|margin|quot/i),
      req('Product catalogue', 'Items, specs and units', /catalog|product|item|spec|bom/i), opt('Approval policy', 'Limits and approver roles', /approv|polic|limit|authori/i)] },
  RFQ: { title: 'RFQ Response Agent', flow: 'RFQ Response: RFQ intake → requirement check → cost inputs → response → approval', item: 'RFQ responses', ready: 'an RFQ response',
    I: 'Read incoming RFQs, check every requirement and prepare a response for human approval before it is sent.',
    S: 'RFQ packages, drawings and specs, and past responses. Supporting files to be uploaded next.',
    A: 'RFQ intake → requirement check → cost inputs → draft response → approval. Check required details before moving on.',
    V: 'Response deadlines, required certifications, approval limits and retry bounds: to be confirmed.',
    O: 'Reviewed RFQ response with a requirement checklist, approval status and an audit trail.',
    uploads: [req('RFQ packages', 'RFQ documents and attachments', /rfq|request|tender|package/i), req('Drawings and specs', 'Drawings, specs and tolerances', /draw|dwg|drg|spec/i),
      req('Past responses', 'Previous quotes or bids', /response|quot|bid|offer/i), opt('Certification list', 'Certificates the buyer asks for', /cert|complian|iso/i)] },
  'Quality engineering': { title: 'Quality Inspection Agent', flow: 'Quality Engineering: inspection data → spec check → nonconformance → review → report', item: 'Nonconformances', ready: 'a nonconformance report',
    I: 'Check inspection results against the specification, raise nonconformances and route them for engineering review.',
    S: 'Inspection records, specifications and quality procedures. Supporting files to be uploaded next.',
    A: 'Inspection data → spec check → nonconformance → review → report. Check required details before moving on.',
    V: 'Tolerances, sampling plans, escalation limits and retry bounds: to be confirmed.',
    O: 'Reviewed inspection report with nonconformances, dispositions and an audit trail.',
    uploads: [req('Inspection records', 'Measurements and inspection reports', /inspect|measur|cmm|report/i), req('Specifications', 'Drawings, tolerances and standards', /spec|draw|toleran|standard/i),
      req('Quality procedures', 'SOPs and control plans', /procedure|sop|qms|control/i), opt('NCR history', 'Past nonconformances and CAPAs', /ncr|nonconform|capa/i)] },
  'Assets management': { title: 'Asset Management Agent', flow: 'Assets Management: asset register → condition check → maintenance plan → approval → work order', item: 'Work orders', ready: 'a work order',
    I: 'Track asset condition, plan maintenance and raise work orders for approval before they are scheduled.',
    S: 'The asset register, maintenance logs and schedules. Supporting files to be uploaded next.',
    A: 'Asset register → condition check → maintenance plan → approval → work order. Check required details before moving on.',
    V: 'Maintenance intervals, criticality levels, cost limits and retry bounds: to be confirmed.',
    O: 'Approved maintenance plan with work orders, asset history and an audit trail.',
    uploads: [req('Asset register', 'Equipment list with locations', /asset|register|equipment/i), req('Maintenance logs', 'Service and repair history', /mainten|log|service|work.?order/i),
      req('Maintenance schedules', 'Preventive maintenance plans', /schedul|plan|\bpm\b/i), opt('Spare parts list', 'Parts, stock and suppliers', /spare|part|inventor/i)] },
  'GTM workflow': { title: 'GTM Workflow Agent', flow: 'GTM Workflow: market brief → segment → campaign plan → approval → launch', item: 'Campaigns', ready: 'a campaign plan',
    I: 'Turn product and market briefs into segmented campaign plans and route them for approval before launch.',
    S: 'Product briefs, target segments and past campaigns. Supporting files to be uploaded next.',
    A: 'Market brief → segment → campaign plan → approval → launch. Check required details before moving on.',
    V: 'Budgets, channels, target segments, approval limits and retry bounds: to be confirmed.',
    O: 'Approved campaign plan with segments, channels, budget and an audit trail.',
    uploads: [req('Product briefs', 'Positioning and product notes', /brief|product|position/i), req('Target segments', 'Ideal customers and personas', /segment|icp|persona|market/i),
      req('Campaign history', 'Past campaigns and results', /campaign|launch|history/i), opt('Brand guidelines', 'Tone, style and assets', /brand|guideline|style/i)] },
  'Lead generation': { title: 'Lead Generation Agent', flow: 'Lead Generation: lead sources → enrich → score → review → hand-off', item: 'Leads', ready: 'a scored lead list',
    I: 'Collect leads, enrich and score them, and route the best ones for review before hand-off to sales.',
    S: 'Lead lists, the ideal customer profile and scoring rules. Supporting files to be uploaded next.',
    A: 'Lead sources → enrich → score → review → hand-off. Check required details before moving on.',
    V: 'Scoring weights, qualification thresholds, volume limits and retry bounds: to be confirmed.',
    O: 'Reviewed, scored lead list with the reasons for each score and an audit trail.',
    uploads: [req('Lead lists', 'Contacts and prospects', /lead|contact|prospect/i), req('Ideal customer profile', 'Who the best customers are', /icp|persona|profile|segment/i),
      req('Scoring rules', 'How leads are qualified', /scor|rule|qualif/i), opt('CRM export', 'Current pipeline', /crm|export|pipeline/i)] },
}
/** A custom ("Others") What: the same generation path, worded from the FDE's own value. */
function customProfile(what: string): Profile {
  const name = cap(what.trim()) || 'Workflow'
  return { title: `${name} Agent`, flow: `${name}: input → processing → review → delivery`, item: 'Items', ready: 'a result',
    I: `Automate ${lowerFirst(what)} and route each result for human review before delivery.`,
    S: 'Sample inputs, business rules and reference data. Supporting files to be uploaded next.',
    A: 'Input → processing → review → delivery. Check required details before moving on.',
    V: 'Limits, thresholds and retry bounds: to be confirmed.',
    O: 'Reviewed result with its sources, approval status and an audit trail.',
    uploads: [req('Sample inputs', 'Real examples of what the agent receives', /sample|input|example|request/i), req('Business rules', 'Rules, rates and exceptions', /rule|rate|guideline|sop|process/i),
      req('Reference data', 'Master data the agent looks up', /reference|master|catalog|data|list/i), opt('Approval policy', 'Limits and approver roles', /approv|polic|limit|authori/i)] }
}
const profileKey = (what: string) => { const w = what.trim().toLowerCase(); return Object.keys(PROFILES).find(k => w === k.toLowerCase() || w.startsWith(k.toLowerCase() + ' ')) }
const profileOf = (g: Goal) => PROFILES[profileKey(g.what) ?? ''] ?? customProfile(g.what)

export const flowLine = (g: Goal) => profileOf(g).flow

export function expandGoal(g: Goal): Draft {
  const p = profileOf(g)
    // The Source names its mandatory files (5 Nov 2026): the profile's required uploads, so the goal says what must be added.
  const mandatory = p.uploads.filter(u => u.required).map(u => u.name)
  const source = p.S.replace(/\.?\s*Supporting files to be uploaded next\.$/, '') + `. Mandatory files: ${mandatory.slice(0, -1).join(', ')}${mandatory.length > 1 ? ' and ' : ''}${mandatory[mandatory.length - 1]}. Supporting files to be uploaded next.`
  const sections: Record<Key, string> = { I: p.I, S: source, A: p.A,
    T: 'Use approved tools from the project context. Connections and tool choices need validation.',
    V: p.V, O: p.O,
    N: `Notify ${owner(g.who)} of missing inputs or ${p.ready} ready for review. Define delivery channels before use.` }
  for (const k of KEYS) sections[k] = esc(sections[k])
  return { goal: g, quote: p === PROFILES.Quoting, item: p.item, title: p.title, subtitle: `For ${lowerFirst(g.who)} in ${g.where.trim()}.`,
    sections, base: { ...sections }, uploads: p.uploads, rev: Date.now() }
}

/** Required (and optional) supporting files no uploaded file name matches yet. */
export const missingUploads = (d: Draft, names: string[]) => d.uploads.filter(u => !names.some(n => u.match.test(n)))
export const changedSections = (d: Draft) => KEYS.filter(k => plain(d.sections[k]) !== plain(d.base[k]))

/** "2 lakh", "₹2,00,000", "200000", "50k", "1.5 crore" → a number. */
export function amountOf(text: string): number | undefined {
  const m = /(?:₹|rs\.?|inr|\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|crore|cr|k|thousand|m|million)?\b/i.exec(text)
  if (!m) return
  const n = parseFloat(m[1].replace(/,/g, '')), unit = (m[2] ?? '').toLowerCase()
  const mult = /^(lakh|lac)$/.test(unit) ? 1e5 : /^(crore|cr)$/.test(unit) ? 1e7 : /^(k|thousand)$/.test(unit) ? 1e3 : /^(m|million)$/.test(unit) ? 1e6 : 1
  return Math.round(n * mult)
}
const money = (n: number, where: string) => /india/i.test(where) ? `₹${n.toLocaleString('en-IN')}` : n.toLocaleString('en-US')
/** Drop "to be confirmed" and any earlier sentence starting with `lead`, then add `line`. */
function setLine(html: string, lead: RegExp, line: string) {
  const kept = html.replace(/:\s*to be confirmed\./i, '.').split(/(?<=\.)\s+/).filter(s => !lead.test(plain(s)) && !/^Check required details/i.test(plain(s)))
  return [...kept, esc(line)].join(' ')
}

export interface Edit { draft: Draft; reply: string }
/** The assistant's draft edits. Returns undefined when the message is not an edit it knows. */
export function applyEdit(d: Draft, text: string): Edit | undefined {
  const s = { ...d.sections }
  const amount = amountOf(text)
  if (amount && /sign-?off|approv|threshold|senior/i.test(text)) {
    const v = money(amount, d.goal.where), item = d.item
    s.A = setLine(s.A, /route to a senior approver/i, `${item} above ${v} route to a senior approver first.`)
    s.V = setLine(s.V, /^Senior sign-off threshold/i, `Senior sign-off threshold: ${v}.`)
    return { draft: { ...d, sections: s, rev: d.rev + 1 },
      reply: `Done. Automation now routes ${item.toLowerCase()} above ${v} to a senior approver before delivery, and Variables carries the threshold so the team can tune it. The draft on the right is updated.` }
  }
  if (/margin/i.test(text) && /guard|minimum|floor|add/i.test(text)) {
    const pct = /(\d+(?:\.\d+)?)\s*%/.exec(text)?.[1] ?? '12'
    s.V = setLine(s.V, /^Minimum margin/i, `Minimum margin: ${pct}% (a starting value; tune it).`)
    s.A = setLine(s.A, /below the minimum margin/i, `${d.item} below the minimum margin go back for review.`)
    return { draft: { ...d, sections: s, rev: d.rev + 1 },
      reply: `Done. Variables now holds a minimum margin of ${pct}% and Automation sends anything below it back for review. The draft on the right is updated.` }
  }
}

/** Questions about the draft on screen (the chips and similar wording). */
export function draftReply(d: Draft, text: string, uploaded: string[]): string | undefined {
  if (/missing|still need|which files/i.test(text)) {
    const miss = missingUploads(d, uploaded), req = miss.filter(u => u.required)
    if (!miss.length) return 'Nothing is missing: every supporting file in Required uploads has a matching file name.'
    return `${req.length ? `Still required: ${req.map(u => `${u.name} (${u.hint.toLowerCase()})`).join(', ')}.` : 'Every required file is there.'}`
      + `${miss.some(u => !u.required) ? ` Optional: ${miss.filter(u => !u.required).map(u => u.name).join(', ')}.` : ''} Matching is by file name only.`
  }
  const into = /^what goes into (.+?)\??$/i.exec(text.trim())
  const u = into && d.uploads.find(x => x.name.toLowerCase() === into[1].trim().toLowerCase())
  if (u) return `${u.name}: ${u.hint.charAt(0).toLowerCase()}${u.hint.slice(1)}. ${u.required ? 'It is required' : 'It is optional'}; any file whose name says what it holds (for example ${u.name.toLowerCase().replace(/\s+/g, '-')}.pdf) is matched.`
  if (/what does isatvon mean/i.test(text))
    return 'ISATVON is the seven-part contract for each skill: Instructions, Source data, Automation, Tech Stack, Variables, Outcome and Next. The draft above fills each part for your goal.'
  if (/what files should i add/i.test(text)) {
    const need = d.uploads.filter(u => u.required)
    return `I need: ${need.map(u => `${u.name} (${u.hint.charAt(0).toLowerCase()}${u.hint.slice(1)})`).join(', ')}. Add them on the left; I match them by file name.`
  }
  if (/what does (the )?margin( guardrail)?( mean| do)\??$/i.test(text.trim()))
    return 'Margin is the share of a quote price that is profit after cost. A minimum margin guardrail stops me from sending a quote below it without a person approving. If you want one, tell me the percentage.'
  if (/what does (the )?sign-?off threshold( do| mean)\??$/i.test(text.trim()))
    return `The sign-off threshold is the ${d.item.toLowerCase()} value above which a senior person must approve before anything goes out. Tell me the amount if you want to set it.`
  if (/variables/i.test(text) && /what|explain|mean|plain/i.test(text))
    return `Variables are the values the team can change without rebuilding the agent. Right now: ${plain(d.sections.V)}`
  if (/what changed|changes|diff/i.test(text)) {
    const ch = changedSections(d)
    return ch.length ? `Changed since the draft was generated: ${ch.map(k => `${SECTION[k]}: "${plain(d.sections[k])}"`).join(' ')}` : 'Nothing has changed since the draft was generated.'
  }
}

/** Suggestions for the draft on screen; they change with it. */
export function draftChips(d: Draft, uploaded: string[]): string[] {
  return [
    missingUploads(d, uploaded).some(u => u.required) && 'Which files are still missing?',
    'What do the Variables mean?',
    d.quote && !/minimum margin/i.test(plain(d.sections.V)) && 'What does margin mean?',
    changedSections(d).length > 0 && 'What changed in the draft?',
  ].filter((c): c is string => !!c)
}

/** Chat-aware suggestions (goal-flow fine-tuning v2, 29 Oct 2026): at most two. Before the person says anything:
 *  the opening pair from the draft. After that: only follow-ups to the last message (an edit, a question about files
 *  or Variables); an unrelated message gets none, so the generic opening set never returns. */
export function conversationChips(d: Draft, uploaded: string[], last: string | null): string[] {
  if (last === null) return draftChips(d, uploaded).slice(0, 2)
  const V = plain(d.sections.V)
  const margin = /minimum margin/i.test(V), threshold = /sign-off threshold/i.test(V)
  const miss = missingUploads(d, uploaded).filter(u => u.required)
  let next: (string | false)[] = []
  if (amountOf(last) && /sign-?off|approv|threshold|senior/i.test(last))
    next = ['What changed in the draft?', 'What does the sign-off threshold do?']
  else if (/margin/i.test(last))
    next = ['What changed in the draft?', 'What does the margin guardrail do?']
  else if (/missing|still need|which files|what goes into/i.test(last)) {
    const asked = /what goes into (.+?)\??$/i.exec(last)?.[1]?.toLowerCase()
    const rest = miss.filter(u => u.name.toLowerCase() !== asked)
    next = rest.length ? rest.slice(0, 2).map(u => `What goes into ${u.name}?`) : ['What do the Variables mean?']
  }
  else if (/variables/i.test(last)) next = [d.quote && !margin && 'What does margin mean?', !threshold && 'What does the sign-off threshold do?']
  else if (/what changed|changes|diff/i.test(last)) next = changedSections(d).includes('V') ? ['What do the Variables mean?'] : []
  return next.filter((c): c is string => !!c && c.toLowerCase() !== last.trim().toLowerCase()).slice(0, 2)
}

/** The output editor's document: title, subtitle, then one heading (letter chip) + paragraph per ISATVON section. */
export const draftHtml = (d: Draft) => `<h2>${esc(d.title)}</h2><p>${esc(d.subtitle)}</p>`
  + KEYS.map(k => `<h3><code>${k}</code> ${SECTION[k]}</h3><p>${d.sections[k]}</p>`).join('')
/** Edits typed in the output editor back into the sections (a section whose heading was removed keeps its text). */
export function readHtml(d: Draft, html: string): Draft {
  const s = { ...d.sections }
  for (const m of html.matchAll(/<h3><code>([ISATVON])<\/code>[^<]*<\/h3><p>(.*?)<\/p>/g)) s[m[1] as Key] = m[2]
  return { ...d, sections: s }
}

// ---- the store (one draft per intake; saved as `bam-goal-v2`) --------------------------------------------------
export interface GoalState { goal: Goal; draft: Draft | null; approved: boolean; /** The goal as typed (shown as the first chat bubble). */ text?: string }
const revive = (d: Draft | null) => d && { ...d, uploads: expandGoal(d.goal).uploads } // RegExps do not survive JSON
// v2 = the dropdown goal (What no longer carries "automates"); the old free-text `bam-goal` is dropped.
const GOAL_KEY = 'bam-goal-v2'
let gs: GoalState = (() => {
  try { localStorage.removeItem('bam-goal') } catch { /* blocked */ }
  try { const s = JSON.parse(localStorage.getItem(GOAL_KEY) ?? 'null') as GoalState | null; if (s) return { ...s, draft: revive(s.draft) } } catch { /* none or blocked */ }
  return { goal: EXAMPLE, draft: null, approved: false }
})()
const gsubs = new Set<() => void>()
export function setGoal(patch: Partial<GoalState> | ((s: GoalState) => Partial<GoalState>)) {
  gs = { ...gs, ...(typeof patch === 'function' ? patch(gs) : patch) }
  try { localStorage.setItem(GOAL_KEY, JSON.stringify(gs)) } catch { /* storage blocked */ }
  gsubs.forEach(f => f())
}
export const getGoal = () => gs
export const resetGoal = () => setGoal({ goal: EXAMPLE, draft: null, approved: false, text: '' })
/** Fresh entry (5 Nov 2026, per user): a (re)load that revives a saved goal always starts at the single-pane goal
 *  entry with the saved goal pre-filled, and Enter continues the saved project exactly as stored (draft edits and
 *  uploaded files survive). Session-scoped only - nothing is cleared from storage. */
let entry = !!gs.draft
export const atGoalEntry = () => entry && !!gs.draft
export function continueGoal() { if (!entry) return; entry = false; gsubs.forEach(f => f()) }
export const useGoal = <T,>(select: (s: GoalState) => T) =>
  useSyncExternalStore(cb => { gsubs.add(cb); return () => { gsubs.delete(cb) } }, () => select(gs))
/** "+ Add files" (4 Nov 2026, no approval gate): opens as soon as the goal is expanded into a draft. */
export const canAddFiles = (s: GoalState) => !!s.draft
/** The one lock every upload path checks (Add files menu, picker, drops, the review dialog's Add more, the uploader). */
export const addFilesLocked = () => !canAddFiles(getGoal())
/** The draft now lives in the middle-pane chat (4 Nov 2026), never in the right pane. */
export const showsDraft = (_s: GoalState, _files: number) => false

/** Pane progression (4 Nov 2026): 1 = middle pane only, 2 = goal expanded (files pane opens), 3 = files added (outputs pane opens). */
export const goalStage = (s: GoalState, files: number): 1 | 2 | 3 => !s.draft || atGoalEntry() ? 1 : files > 0 ? 3 : 2

/** Splits a typed goal "<what> for <who> in <where>" into the three parts the profile reads. Free-form text is valid:
 *  parts that are not written fall back to neutral wording, and the whole text stays the What. */
export function parseGoal(text: string): Goal {
  const t = text.trim().replace(/^build an ai agent that automates\s+/i, '').replace(/[.\s]+$/, '')
  const m = /^(.*?)\s+for\s+(.*?)(?:\s+in\s+(.+))?$/i.exec(t)
  if (m && m[1].trim() && m[2].trim()) return { what: m[1].trim(), who: m[2].trim(), where: (m[3] ?? 'your market').trim() }
  return { what: t, who: 'your team', where: 'your market' }
}
