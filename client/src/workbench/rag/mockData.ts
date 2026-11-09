// Illustrative data for the stage 2 EXAMPLE MOCKUP screens. Not measured, not from a real Cognee/OKF run.

export type OkfClass = 'Document' | 'Table' | 'CADGeometry' | 'Correspondence' | 'Unclassified'

export interface ExtractionJob {
  object: string
  s3: string
  okfClass: OkfClass
  extractor: string
  entities: number
  confidence: number | null
}

export const JOBS: ExtractionJob[] = [
  { object: 'RFQ_Package_Meridian.pdf', s3: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf', okfClass: 'Document', extractor: 'pypdf + entity extraction', entities: 9, confidence: 0.94 },
  { object: 'BOM_RevC.xlsx', s3: 's3://bam-intake/rfq-2026-0147/BOM_RevC.xlsx', okfClass: 'Table', extractor: 'openpyxl', entities: 16, confidence: 0.99 },
  { object: 'MPW-4421-bracket.step', s3: 's3://bam-intake/rfq-2026-0147/MPW-4421-bracket.step', okfClass: 'CADGeometry', extractor: 'cadquery', entities: 102, confidence: null },
  { object: 'MPW-4422-housing.step', s3: 's3://bam-intake/rfq-2026-0147/MPW-4422-housing.step', okfClass: 'CADGeometry', extractor: 'cadquery', entities: 84, confidence: null },
  { object: 'email-thread.eml', s3: 's3://bam-intake/rfq-2026-0147/email-thread.eml', okfClass: 'Correspondence', extractor: 'mailparser', entities: 3, confidence: 0.91 },
  { object: 'meridian-logo.png', s3: 's3://bam-intake/rfq-2026-0147/meridian-logo.png', okfClass: 'Unclassified', extractor: '—', entities: 0, confidence: null },
]

export const COVERAGE_META: Record<OkfClass, string> = {
  Document: 'RFQ pdf · Spec QA-101',
  Table: 'BOM lines + headers',
  CADGeometry: '2 STEP files · bodies/faces',
  Correspondence: 'email-thread.eml',
  Unclassified: 'file type not in the accepted intake types (Document checklist)',
}

/** Graph filter chip counts as shown in the 2.1 mockup. */
export const CLASS_COUNTS: [OkfClass, number][] = [['Document', 9], ['Table', 2], ['CADGeometry', 2], ['Correspondence', 1]]

export type NodeKind = 'request' | 'document' | 'table' | 'cad' | 'correspondence' | 'material' | 'customer'

export interface GraphNodeDef {
  id: string
  label: string
  sub?: string
  kind: NodeKind
  okfClass: string
  /** Filter chip this node belongs to, if any. */
  filter?: OkfClass
  x: number
  y: number
  /** Appears once this many extraction jobs have finished. */
  after: number
  sources: string[]
  relations: string
  facts: string
  confidence: number
}

// Laid out around the root (rfq, 400/250): 2.1 pins the root at the canvas centre.
export const NODES: GraphNodeDef[] = [
  { id: 'rfq', label: 'RFQ-2026-0147', kind: 'request', okfClass: 'Request', x: 400, y: 250, after: 1, sources: ['RFQ_Package_Meridian.pdf'], relations: '3 docs · 1 customer', facts: '"quote due 2026-10-20" · "Net 45 terms"', confidence: 0.97 },
  { id: 'meridian', label: 'Meridian', sub: 'customer', kind: 'customer', okfClass: 'Customer', x: 430, y: 430, after: 5, sources: ['email-thread.eml', 'RFQ_Package_Meridian.pdf'], relations: '1 RFQ · 2 materials', facts: '"repeat aerospace customer"', confidence: 0.96 },
  { id: 'bom', label: 'BOM_RevC', sub: '.xlsx', kind: 'table', okfClass: 'Table', filter: 'Table', x: 180, y: 130, after: 2, sources: ['BOM_RevC.xlsx'], relations: '2 parts · 2 materials', facts: '"14 rows parsed, 2 flagged" · "Rev C line 2 qty bump"', confidence: 0.99 },
  { id: 'pkg', label: 'RFQ_Package', sub: '.pdf', kind: 'document', okfClass: 'Document', filter: 'Document', x: 590, y: 125, after: 1, sources: ['RFQ_Package_Meridian.pdf'], relations: '2 materials · 1 spec', facts: '"target pricing" · "ASTM D3951 packaging"', confidence: 0.94 },
  { id: 'spec', label: 'Spec_QA-', sub: '101.pdf', kind: 'document', okfClass: 'Document', filter: 'Document', x: 700, y: 290, after: 1, sources: ['RFQ_Package_Meridian.pdf'], relations: '1 material · 1 cert', facts: '"mill cert per lot" · "ultrasonic inspection"', confidence: 0.95 },
  { id: 'email', label: 'email-thread', sub: '.eml', kind: 'correspondence', okfClass: 'Correspondence', filter: 'Correspondence', x: 570, y: 355, after: 5, sources: ['email-thread.eml'], relations: '1 customer · 1 revision', facts: '"Rev C replaces Rev B" · "lead time 4 wks ARO"', confidence: 0.91 },
  { id: 'step', label: 'MPW-4421', sub: '.step', kind: 'cad', okfClass: 'CADGeometry', filter: 'CADGeometry', x: 150, y: 345, after: 3, sources: ['MPW-4421-bracket.step'], relations: '1 BOM line · 1 material', facts: '"102 faces, 1 body" · "±0.05mm critical bore"', confidence: 0.92 },
  { id: 'housing', label: 'MPW-4422', sub: '.step', kind: 'cad', okfClass: 'CADGeometry', filter: 'CADGeometry', x: 390, y: 95, after: 4, sources: ['MPW-4422-housing.step'], relations: '1 BOM line · 1 material', facts: '"84 faces, 1 body" · "high-pressure sealing"', confidence: 0.93 },
  { id: 'al', label: '6061-T6', sub: 'material', kind: 'material', okfClass: 'Material', x: 290, y: 365, after: 2, sources: ['BOM_RevC.xlsx'], relations: '1 part · 1 finish', facts: '"MIL-A-8625 Type II clear anodize" · "±0.1mm"', confidence: 0.96 },
  { id: 'ss', label: '17-4PH SS', sub: 'material', kind: 'material', okfClass: 'Material', x: 600, y: 235, after: 4, sources: ['RFQ_Package_Meridian.pdf', 'MPW-4422-housing.step'], relations: '1 part · 1 spec', facts: '"EN 10204 3.1 cert required" · "line 2 housing only"', confidence: 0.95 },
]

export const EDGES: { source: string; target: string; label?: string }[] = [
  { source: 'rfq', target: 'bom', label: 'contains_bom' },
  { source: 'rfq', target: 'pkg', label: 'contains_spec' },
  { source: 'rfq', target: 'email', label: 'clarified_by' },
  { source: 'rfq', target: 'meridian', label: 'issued_by' },
  { source: 'bom', target: 'step', label: 'specifies_part' },
  { source: 'bom', target: 'housing', label: 'specifies_part' },
  { source: 'step', target: 'al', label: 'uses_material' },
  { source: 'housing', target: 'ss', label: 'uses_material' },
  { source: 'pkg', target: 'spec', label: 'references_spec' },
  { source: 'spec', target: 'ss', label: 'governs_cert' },
  { source: 'email', target: 'bom', label: 'amends_revision' },
  { source: 'al', target: 'meridian', label: 'quoted_for' },
  { source: 'ss', target: 'meridian', label: 'quoted_for' },
]

export interface SchemaDef {
  name: string
  okfSource: string
  fields: string
  mapsTo: string
  status: 'reviewed' | 'selected' | 'draft'
  code: string
}

export const SCHEMAS: SchemaDef[] = [
  { name: 'Requirement', okfSource: 'Document · RFQ pdf', fields: 'id, text, category, tolerance, source_page', mapsTo: 'BAM-RFQ-001-01', status: 'reviewed',
    code: 'class Requirement(BaseModel):\n    id: str\n    text: str\n    category: Literal["dimensional", "material", "commercial"]\n    tolerance: str | None\n    source_page: int\n\n# source: RFQ_Package_Meridian.pdf\n# 9 requirements extracted\n' },
  { name: 'QuoteLine', okfSource: 'Table · BOM xlsx', fields: 'line_no, part_no, desc, material, qty, unit_price', mapsTo: 'BAM-RFQ-001-02/04', status: 'selected',
    code: 'class QuoteLine(BaseModel):\n    line_no: int\n    part_no: str  # MPW-NNNN\n    description: str\n    material: Material\n    qty: int = Field(gt=0)\n    unit_price: Decimal | None\n    flags: list[ExceptionRecord]\n\n# source: BOM_RevC.xlsx · sheet "BOM"\n# 14 rows parsed · 2 flagged\n' },
  { name: 'ExceptionRecord', okfSource: 'Table + Rules master', fields: 'rule_id, line_ref, severity, route, resolved_by', mapsTo: 'BAM-RFQ-001-03', status: 'draft',
    code: 'class ExceptionRecord(BaseModel):\n    rule_id: str\n    line_ref: int\n    severity: Literal["low", "high", "blocking"]\n    route: str\n    resolved_by: str | None\n\n# source: BOM_RevC.xlsx + Rules master\n' },
  { name: 'RFQSession', okfSource: 'Correspondence · eml', fields: 'customer, due_date, parts_count, completeness', mapsTo: 'BAM-RFQ-001', status: 'draft',
    code: 'class RFQSession(BaseModel):\n    customer: str\n    due_date: date\n    parts_count: int\n    completeness: float = Field(ge=0, le=1)\n\n# source: email-thread.eml\n' },
  { name: 'MaterialCert', okfSource: 'Document · Spec QA-101', fields: 'material, cert_type, required, attached', mapsTo: 'BAM-QA-002-01', status: 'draft',
    code: 'class MaterialCert(BaseModel):\n    material: Material\n    cert_type: Literal["mill", "conformance"]\n    required: bool\n    attached: bool\n\n# source: Spec_QA-101.pdf\n' },
]

export interface Citation { label: string; s3: string }
export interface QaAnswer { match: RegExp; answer: string; citations: Citation[] }

export const QA: QaAnswer[] = [
  {
    match: /17-4|cert/i,
    answer: 'MPW-4422 (housing) is the only 17-4PH part in RFQ-2026-0147. Spec QA-101 requires a mill cert per lot. Target price is missing on line 2.',
    citations: [
      { label: 'BOM_RevC.xlsx · line 2', s3: 's3://bam-intake/rfq-2026-0147/BOM_RevC.xlsx#line=2' },
      { label: 'Spec_QA-101.pdf · §3.2', s3: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf#spec-qa-101' },
      { label: 'RFQ pdf · p.4', s3: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf#page=4' },
    ],
  },
  {
    match: /toleranc|4421/i,
    answer: 'MPW-4421 (bracket) is 6061-T6 with a general tolerance of ±0.1 mm; critical bore hole pattern is ±0.05 mm. Surface finish Ra 0.8 µm with MIL-A-8625 Type II clear anodize.',
    citations: [
      { label: 'MPW-4421-bracket.step', s3: 's3://bam-intake/rfq-2026-0147/MPW-4421-bracket.step' },
      { label: 'RFQ pdf · p.6', s3: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf#page=6' },
    ],
  },
  {
    match: /rev ?c|change/i,
    answer: 'Rev C replaces Rev B: line 2 material changed to 17-4PH SS and quantity of MPW-4421 increased from 200 to 250 units. Delivery date updated to 4 weeks ARO.',
    citations: [
      { label: 'email-thread.eml', s3: 's3://bam-intake/rfq-2026-0147/email-thread.eml' },
      { label: 'BOM_RevC.xlsx · line 2', s3: 's3://bam-intake/rfq-2026-0147/BOM_RevC.xlsx#line=2' },
    ],
  },
  {
    match: /deliver|terms|payment|net 45/i,
    answer: 'Delivery is required within 4 weeks from purchase order receipt. Packaging per ASTM D3951 industrial standards with vapor corrosion inhibitor bags. Commercial terms are Net 45.',
    citations: [
      { label: 'RFQ_Package_Meridian.pdf · §4', s3: 's3://bam-intake/rfq-2026-0147/RFQ_Package_Meridian.pdf#page=4' },
      { label: 'Terms_and_Conditions.pdf', s3: 's3://bam-intake/rfq-2026-0147/Terms_and_Conditions.pdf' },
    ],
  },
  {
    match: /org|compliance|iso|as9100|itar|gpu/i,
    answer: 'ORG-EMS / BU-AERO-01 governs Tier-1 EMS contract manufacturing with AS9100D, ISO 9001 and ITAR registration. ERP integration links SAP S/4HANA & DigiKey APIs, backed by on-prem GPU cluster (2x H100 nodes). Ref: MFG-RES-001 & MFG-RES-002.',
    citations: [
      { label: 'Manufacturing_Masters_v0.2.xlsx · Organization (ORG-EMS)', s3: 's3://bam-intake/masters/Organization.xlsx#ORG-EMS' },
      { label: 'MFG-RES-001 · Source Registry', s3: 'https://docs.google.com/spreadsheets/d/1MKBnjBWeQedg5INCuSL95LXRD3VDn3bkeSiUxqpctDY' },
    ],
  },
  {
    match: /costing|rate|scrap|waste|dfm|rule/i,
    answer: 'Rule #1 & RULE-BOM/RULE-OFFER establish PCBA costing: SMT Line $120/hr, Manual Assembly $45/hr, Testing $65/hr. Standard scrap rate 1.5%, waste factor 2.0%. 155 BOM QA validation checks per MFG-RES-003, with quote approval gate MFG-RES-004.',
    citations: [
      { label: 'Manufacturing_Masters_v0.2.xlsx · Rules (Rule #1)', s3: 's3://bam-intake/masters/Rules.xlsx#RULE-01' },
      { label: 'MFG-RES-003 · BOM QA Checklist v3.0 (155 checks)', s3: 'https://docs.google.com/spreadsheets/d/1Oe1l6GM0XWQATDgTsixMiMROr-h3w-Zu' },
    ],
  },
  {
    match: /agent|mcp|parser|llama|mouser/i,
    answer: 'Agent master configures Llama-3.3-70B for BOM extraction, OpenAPI v3 for Mouser pricing, FastMCP proxy gateway at mcp://supplier-api-proxy:8080. Roles AG-INTAKE, AG-OFFER, AG-COST, and AG-QA enforce human review before report delivery (MFG-RES-005).',
    citations: [
      { label: 'Manufacturing_Masters_v0.2.xlsx · Agents (Agent #1)', s3: 's3://bam-intake/masters/Agents.xlsx#AG-01' },
      { label: 'MFG-RES-005 · BuLLM FastMCP Runtime Binding', s3: 's3://bam-intake/masters/Agents.xlsx#MFG-RES-005' },
    ],
  },
  {
    match: /isatvon|skill|loopy|workflow|loop|notification/i,
    answer: 'ISATVON 1.4 (Loopy-Enhanced) defines Declarative Bounded Iteration: Action-and-Check cycle with max_iterations: 3, budget_limit: 5000ms. O-Outcome appends state handoff to PostgreSQL JSONB; N-Notification pushes real-time UI states (Verifying -> Looping [Pass 2/3] -> Authenticated), DB updates, and escalation alerts.',
    citations: [
      { label: 'Manufacturing_Masters_v0.2.xlsx · Skills (SK-INTAKE..SK-CORE)', s3: 's3://bam-intake/masters/Skills.xlsx#SK-01' },
      { label: 'ISATVON_1.4_Loopy_Spec.md · Section N (Notifications)', s3: 's3://bam-intake/specs/ISATVON_1.4.md' },
    ],
  },
  {
    match: /filetype|format|tier|ipc|gerber/i,
    answer: 'Filetype master freezes 43 enterprise formats across 3 RAG tiers: Tier 1 (AST/syntax code, OpenAPI, FastMCP), Tier 2 (PDF, XLSX, CSV, DOCX), Tier 3 (CAD STEP/STP, DWG, DXF, Gerber RS-274X/X2/X3, IPC-2581). Intent classes route files to Skills, Rules, Agents, or Organization.',
    citations: [
      { label: 'Manufacturing_Masters_v0.2.xlsx · Filetype (43 frozen types)', s3: 's3://bam-intake/masters/Filetype.xlsx' },
    ],
  },
]

export const QA_SUGGESTIONS = [
  'Which parts use 17-4PH and need certs?',
  'Tolerances and surface finish on MPW-4421?',
  'What did Meridian change in Rev C?',
  'Confirm ITAR, AS9100D & ISO 9001 compliance',
  'What are the machine hour rates and scrap factors in Rules master?',
  'Explain ISATVON 1.4 Loopy iteration bounds and N-Notification updates',
  'Which FastMCP agents handle BOM extraction and pricing?',
]
