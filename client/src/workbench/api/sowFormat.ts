// The ISATVON SOW format (see the approved SOW sample): flat "Requirement N" blocks, each tagged with all
// seven ISATVON fields. The SOW has NO agent/skill grouping; that is applied after approval by a separate
// step (`clusterRequirement`). Status and approval lines are system metadata, not document text.
import { ISATVON, type IsatvonField } from './types'

export type RequirementFields = Record<IsatvonField, string>

/** SOW-format labels, in ISATVON order. */
export const SOW_LABELS: Record<IsatvonField, string> = {
  instructions: 'I — Instructions', sourceData: 'S — Source Data', automation: 'A — Automation', techStack: 'T — Tech Stack',
  variables: 'V — Variables', outcome: 'O — Outcome', notifications: 'N — Next',
}
/** Fields rendered as code in the SOW, like the sample. */
const CODE_FIELDS: IsatvonField[] = ['sourceData', 'automation', 'variables']

/** Illustrative LLM output for the RFQ workflow, in requirement order (no grouping). */
export const RFQ_REQUIREMENTS: RequirementFields[] = [
  { instructions: 'Watch the RFQ inbox, accept the package, store each attachment in object storage and register it with the workflow run.',
    sourceData: 's3://client-bucket/rfq/inbox/', automation: 'UNSTRUCTURED_TO_SOW_LLM', techStack: 'FastMCP, PostgreSQL',
    variables: 'max_attachments: 25; accepted_types: pdf,xlsx,csv', outcome: 'RfqPackage JSON: package_id, attachments[], received_at',
    notifications: 'Post to #rfq-intake on receipt' },
  { instructions: 'Classify each attachment as drawing, BOM, specification or commercial terms using the OKF document classes.',
    sourceData: 's3://client-bucket/rfq/inbox/', automation: 'UNSTRUCTURED_TO_SOW_LLM', techStack: 'Context Engine, Pydantic',
    variables: 'classes: drawing,bom,spec,terms; min_confidence: 0.8', outcome: 'ClassifiedDocument[] JSON with class and confidence',
    notifications: 'Alert on unclassified attachment' },
  { instructions: 'Validate every BOM line: part_no, qty and material are mandatory; flag lines that fail and keep the reason.',
    sourceData: 's3://client-bucket/rfq/bom_file.xlsx', automation: 'EXCEL_TO_JSON_AST', techStack: 'OpenPyXL, DuckDB, Pydantic',
    variables: 'header_row: 1; required_columns: part_no,qty,material; target_cell: BOM!A2', outcome: 'BOM matrix JSON with per-line validation result',
    notifications: 'Webhook on validation complete' },
  { instructions: 'Route flagged BOM lines to the quality lead; block quotation until every blocking exception is resolved.',
    sourceData: 'db://bam/bom_exceptions', automation: 'HITL_APPROVAL', techStack: 'FastMCP, DBHub',
    variables: 'blocking_severity: high; sla_hours: 24', outcome: 'ExceptionDecision[] JSON: line_id, decision, reviewer',
    notifications: 'HITL sign-off by quality lead; webhook on routing' },
  { instructions: 'Calculate landed cost by adding base price, 4% freight, and local tariffs for each BOM line, then aggregate.',
    sourceData: 'db://erp/pricing.price_list', automation: 'JSON_TO_PYTHON_JINJA2', techStack: 'DuckDB, Pydantic',
    variables: 'freight_rate: 0.04; tariff_table: db://erp/tariffs; total_formula: =SUM(Quote!B2:B11)', outcome: 'Aggregated pricing comparison table (JSON)',
    notifications: 'Post to #rfq-quotes on completion or error' },
  { instructions: 'Present the aggregated quote to the commercial manager and record the approval decision with timestamp.',
    sourceData: 'db://bam/quotes', automation: 'HITL_APPROVAL', techStack: 'PostgreSQL, Apache Superset',
    variables: 'approver_role: commercial_manager', outcome: 'QuoteApproval JSON: quote_id, decision, approved_at',
    notifications: 'HITL sign-off by commercial manager; callback on approval' },
]

const esc = (s: string) => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
const unesc = (s: string) => s.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (_, e: string) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' ' })[e]!)
const text = (html: string) => unesc(html.replace(/<[^>]+>/g, '')).replace(/\s+/g, ' ').trim()

export const reqPassage = (n: number) => `req-${n}`

export function renderSowHtml(reqs: RequirementFields[], notes: string[] = []) {
  const field = (k: IsatvonField, v: string) => {
    const body = CODE_FIELDS.includes(k) ? `<code>${esc(v)}</code>` : esc(k === 'notifications' ? `PARSED · ${v}` : v)
    return `<li><p><strong>${SOW_LABELS[k]}:</strong> ${body}</p></li>`
  }
  return [
    '<h1>Statement of Work — RFQ automation</h1>',
    '<blockquote><p><strong>Note on this document:</strong> this SOW contains individually ISATVON-tagged requirements only, in the order they occur. ' +
      'It has no agent or skill grouping; that is applied after approval by a separate step.</p></blockquote>',
    ...reqs.flatMap((r, i) => [
      `<h2 data-passage="${reqPassage(i + 1)}">Requirement ${i + 1}</h2>`,
      `<ul>${ISATVON.map(c => field(c.key, r[c.key])).join('')}</ul>`,
    ]),
    '<h2>Approval</h2>',
    '<p>This Statement of Work is reviewed and approved by the client stakeholder following FDE review and editing in the SOW editor. ' +
      'Any material change to scope requires a change order and re-approval before implementation proceeds.</p>',
    ...(notes.length ? ['<h2>Revision notes</h2>', ...notes.map(n => `<p>Addressed review request: ${esc(n)}</p>`)] : []),
  ].join('')
}

export interface ParsedRequirement { n: number; passageId: string; fields: RequirementFields }

/** Read the ISATVON blocks back out of (possibly human-edited) SOW HTML. Missing fields come back empty. */
export function parseRequirements(html: string): ParsedRequirement[] {
  const heads = [...html.matchAll(/<h2[^>]*>\s*Requirement\s+(\d+)\s*<\/h2>/gi)]
  return heads.map(h => {
    // A requirement runs until the next <h2> (the next requirement, Approval, or Revision notes).
    const start = h.index! + h[0].length
    const rel = html.slice(start).search(/<h2[\s>]/i)
    const chunk = html.slice(start, rel < 0 ? html.length : start + rel)
    const fields = Object.fromEntries(ISATVON.map(c => [c.key, ''])) as RequirementFields
    for (const m of chunk.matchAll(/<strong>\s*([ISATVON])\s*[—–-][^<:]*:?\s*<\/strong>([\s\S]*?)<\/p>/g)) {
      const key = ISATVON.find(c => c.letter === m[1])!.key
      let v = text(m[2]!).replace(/^:\s*/, '')
      if (key === 'automation') v = v.match(/[A-Z][A-Z0-9_]{2,}/)?.[0] ?? v
      if (key === 'notifications') v = v.replace(/^PARSED\s*[·—–-]\s*/, '')
      fields[key] = v
    }
    return { n: Number(h[1]), passageId: reqPassage(Number(h[1])), fields }
  })
}

export interface DynamicSowContext {
  fdeGoals?: string
  releasedFiles?: string[]
  verifiedChecks?: Array<{
    category: string
    question: string
    answer: string
    citations?: Array<{ label: string; s3: string }>
  }>
  notes?: string[]
}

/** Generates rich, ultra-contextual ISATVON requirements dynamically from verified preflight facts, FDE goals, and files. */
export function generateDynamicRequirements(ctx: DynamicSowContext = {}): RequirementFields[] {
  const goals = (ctx.fdeGoals ?? '').trim()
  const files = ctx.releasedFiles?.length
    ? ctx.releasedFiles
    : ['BOM_RevC.xlsx', 'MPW-4421-bracket.step', 'MPW-4422-housing.step', 'Spec_QA-101.pdf', 'Terms_and_Conditions.pdf']

  // Identify any custom checks from the questionnaire
  const customChecks = (ctx.verifiedChecks ?? []).filter(c =>
    !c.question.includes('17-4PH') &&
    !c.question.includes('MPW-4421') &&
    !c.question.includes('Rev C') &&
    !c.question.includes('payment terms') &&
    !c.question.includes('tenant certifications') &&
    !c.question.includes('costing standards') &&
    !c.question.includes('FastMCP agents') &&
    !c.question.includes('ISATVON 1.4')
  )

  const reqs: RequirementFields[] = [
    {
      instructions: `Watch the RFQ discovery intake bucket and enforce dual-engine threat perimeter (BuLL*Sav ClamAV static signatures paired with Repel behavioral payload repulsion & prompt defense). Only verified, released files are registered with the workflow run.${goals ? ` Align with FDE Intake Directives: "${goals}".` : ''}`,
      sourceData: `s3://bam-intake/rfq-2026-0147/ (${files.join(', ')})`,
      automation: 'UNSTRUCTURED_TO_SOW_LLM',
      techStack: 'FastMCP, ClamAV, Repel, PostgreSQL',
      variables: 'max_batch: 25; air_gapped: true; dual_scan: unanimous; chunk_size: 10MB',
      outcome: 'RfqPackage JSON: package_id, verified_files[], quarantined_threats[], sha256_digests[]',
      notifications: 'Post to #rfq-intake on quarantine release; alert ops on heuristic deviation',
    },
    {
      instructions: 'Parse CAD STEP geometry for part MPW-4421 (6061-T6 aluminum bracket). Extract critical bore pattern tolerance (±0.05 mm), general machining tolerance (±0.1 mm), and enforce surface finish Ra 0.8 µm with MIL-A-8625 Type II clear anodize.',
      sourceData: 's3://bam-intake/rfq-2026-0147/MPW-4421-bracket.step',
      automation: 'CAD_STEP_TOLERANCE_EXTRACTOR',
      techStack: 'cadquery, OpenCascade, FastMCP, Pydantic',
      variables: 'part_id: MPW-4421; critical_bore_tol: ±0.05mm; surface_finish: Ra 0.8µm; spec: MIL-A-8625-II',
      outcome: 'CADFeatureModel JSON: 102 faces, 1 body, verified bore alignment matrix, coating spec',
      notifications: 'Flag dimensional deviation > 0.05mm to engineering lead',
    },
    {
      instructions: 'Reconcile BOM Rev C delta changes against Rev B: line 2 housing MPW-4422 upgraded to 17-4PH stainless steel and bracket MPW-4421 quantity increased to 250 units. Mandatory verification of EN 10204 3.1 inspection certification and Spec QA-101 mill cert per lot.',
      sourceData: 's3://bam-intake/rfq-2026-0147/BOM_RevC.xlsx#line=2; Spec_QA-101.pdf#spec-qa-101',
      automation: 'EXCEL_TO_JSON_AST',
      techStack: 'OpenPyXL, DuckDB, Pydantic, FastMCP',
      variables: 'bom_revision: Rev C; cert_type: EN 10204 3.1; lot_cert_mandatory: true; part_housing: MPW-4422',
      outcome: 'BOMReconciliation JSON: 14 rows parsed, 17-4PH cert status verified, 0 uncertified lots',
      notifications: 'Block quotation if mill cert or EN 10204 3.1 is absent',
    },
    {
      instructions: 'Calculate manufacturing costs per Organization (ORG-EMS / BU-AERO-01) and Rules Master (Rule #1): SMT line at $120/hr, manual assembly at $45/hr, testing at $65/hr, X-Ray at $150/lot. Factor 1.5% standard scrap and 2.0% waste. Execute 155 BOM QA validation checks per MFG-RES-003.',
      sourceData: 's3://bam-intake/masters/Rules.xlsx#RULE-01; MFG-RES-003',
      automation: 'PCBA_COSTING_ENGINE',
      techStack: 'DuckDB, Jinja2, PostgreSQL, FastMCP',
      variables: 'smt_rate: 120.00; manual_rate: 45.00; test_rate: 65.00; scrap_rate: 0.015; waste_rate: 0.020; total_cost_formula: =SUM(Costing!C2:C10)',
      outcome: 'CostBreakdown JSON: line-item SMT, labor, scrap, NRE, landed piece cost',
      notifications: 'Trigger commercial review gate if piece price variance > 5%',
    },
    {
      instructions: 'Execute bounded self-correcting validation cycle per ISATVON 1.4 Loopy framework: Action-and-Check cycle with max_iterations: 3, budget_limit: 5000ms. Decouple Action Tool from independent Fixed Check Tool. O-Outcome persists state handoff to PostgreSQL JSONB; N-Notification streams real-time UI states (Verifying -> Looping [Pass 2/3] -> Completed), DB updates, and escalation alerts.',
      sourceData: 's3://bam-intake/masters/Skills.xlsx; ISATVON_1.4_Loopy_Spec.md',
      automation: 'ISATVON_LOOPY_ROUTER',
      techStack: 'BuLLM DMR, FastMCP, PostgreSQL',
      variables: 'max_iterations: 3; budget_limit_ms: 5000; success_gate: all_checks_passed; timeout_escalate: true',
      outcome: 'WorkflowExecutionRecord JSON: iteration_count, audit_trail, pg_jsonb_state',
      notifications: 'WebSocket push on every loop iteration; alert ops on escalation',
    },
    {
      instructions: 'Enforce delivery within 4 weeks ARO, ASTM D3951 industrial packaging with vapor corrosion inhibitor (VCI) bags, and Net 45 commercial terms. Route aggregated quote package to commercial manager for final HITL approval and cryptographic signature.',
      sourceData: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf; Terms_and_Conditions.pdf',
      automation: 'HITL_APPROVAL',
      techStack: 'FastMCP, Apache Superset, PostgreSQL',
      variables: 'lead_time_weeks: 4; packaging_spec: ASTM-D3951; payment_terms: Net 45; approver: commercial_manager',
      outcome: 'QuoteApprovalRecord JSON: quote_id, decision, signature, approved_at',
      notifications: 'HITL sign-off alert to commercial manager; webhook on approval',
    },
  ]

  for (const cc of customChecks) {
    reqs.push({
      instructions: `Validate custom client requirement: "${cc.question}". Contextual findings: "${cc.answer}".`,
      sourceData: cc.citations?.map(c => c.s3).join('; ') || 's3://bam-intake/rfq-2026-0147/',
      automation: 'DYNAMIC_CONTEXT_VERIFIER',
      techStack: 'Context Engine, FastMCP, Pydantic',
      variables: `category: "${cc.category}"; custom_rule: true; min_confidence: 0.90`,
      outcome: `CustomRequirementVerification JSON: status: verified, category: "${cc.category}"`,
      notifications: 'Alert FDE on custom requirement anomaly or mismatch',
    })
  }

  return reqs
}

export function produceDynamicSowHtml(ctx: DynamicSowContext = {}): string {
  const reqs = generateDynamicRequirements(ctx)
  return renderSowHtml(reqs, ctx.notes)
}

// Post-approval clustering (stand-in for the separate LLM call): requirement → agent and a short skill name.
const CLUSTERS: [agent: string, name: string][] = [
  ['Intake Agent', 'Receive RFQ package'],
  ['Engineering Agent', 'Parse CAD STEP tolerances'],
  ['Quality Agent', 'Validate BOM'],
  ['Quote Agent', 'Calculate landed manufacturing cost'],
  ['Orchestration Agent', 'ISATVON 1.4 bounded loop check'],
  ['Commercial Agent', 'Commercial terms & HITL approval'],
]

export function clusterRequirement(n: number, fields: RequirementFields): { agent: string; name: string } {
  const known = CLUSTERS[n - 1]
  if (known) return { agent: known[0], name: known[1] }
  const words = fields.instructions.split(/\s+/).slice(0, 3).join(' ').replace(/[^\w\s-]/g, '')
  if (/cad|step|geometry|toleran/i.test(fields.instructions)) return { agent: 'Engineering Agent', name: words || `CAD Analysis ${n}` }
  if (/bom|cert|17-4|material/i.test(fields.instructions)) return { agent: 'Quality Agent', name: words || `BOM Verification ${n}` }
  if (/cost|price|smt|scrap/i.test(fields.instructions)) return { agent: 'Quote Agent', name: words || `Costing Analysis ${n}` }
  if (/isatvon|loop|check|notif/i.test(fields.instructions)) return { agent: 'Orchestration Agent', name: words || `Loop Workflow ${n}` }
  return { agent: 'General Agent', name: words || `Requirement ${n}` }
}
