// Domain types shared by the adapter, the mock backend and the UI.
// Every status the UI shows comes from one of these backend-owned objects.

export type RunStatus = 'unknown' | 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped'

export interface Stage {
  key: string
  label: string
  status: RunStatus
  runId?: string
  startedAt?: string
  endedAt?: string
  detail?: string
  error?: string
}

export type StepId = 1 | 2 | 3 | 4
export type StepState = 'locked' | 'active' | 'needs_review' | 'done' | 'failed'

export interface StepInfo {
  id: StepId
  title: string
  state: StepState
  reason: string
  stale: boolean
  editable: boolean
  background: number
}

/** The three scan verdict states shown in 1.1. A scanner/transport failure is `scanError`, never a verdict. */
export type ScanState = 'scanning' | 'verified' | 'blocked'

export interface UploadRecord {
  id: string
  /** The active intake batch this file belongs to. */
  batchId: string
  name: string
  size: number
  status: 'stored' | 'failed'
  error?: string
  uploadedAt: string
  /** Service-returned storage path (quarantine until released). Display metadata only, never a link. */
  objectKey: string
  /** Number of 10 MB parts the file was sent in (1 = single request). */
  parts: number
  scan: ScanState
  scanStartedAt?: string
  scanEndedAt?: string
  /** Safe one-line threat summary for a blocked file (never raw scanner output). */
  threat?: string
  /** Scanner outage, timeout or malformed verdict. The file stays ineligible until a rescan resolves it. */
  scanError?: string
  /** True only after a clean verdict released the object to the clean RAG input location. */
  released: boolean
  /** Tier-1 ClamAV static signature scan verdict. */
  clamavVerdict?: 'clean' | 'threat' | 'pending'
  /** Tier-2 Repel heuristic & adversarial prompt injection verdict. */
  repelVerdict?: 'clean' | 'threat' | 'pending'
  /** Detailed Repel evasion score and heuristic telemetry. */
  repelDetails?: {
    adversarialEvasionScore: number
    promptInjectionRisk: 'none' | 'low' | 'high'
    heuristicFlags: string[]
  }
}

export interface ScanEvent {
  id: string
  fileId: string
  name: string
  kind: 'staged' | 'started' | 'clean' | 'threat' | 'removed' | 'error'
  at: string
}

/** The last ingestion run started from 1.1 (eligible released files only). */
export interface IngestionRun { runId: string; at: string; fileIds: string[] }

export interface Sow {
  version: number
  generatedVersion: number
  html: string
  /** The model's output for `generatedVersion`; compared against `html` to show human edits. */
  generatedHtml: string
  /** The saved version just before this one (an edit or a regeneration); the default comparison base. */
  previous?: { version: number; html: string; at: string }
  /** System metadata shown above the document (never editable document text). */
  meta: { sowId: string; client: string; lengthMode: string; sources: number }
  /** The RAG run whose context generated this SOW; a newer RAG run makes downstream work "from a previous run". */
  contextRunId?: string
  updatedAt: string
  status: 'draft' | 'regenerating' | 'approved'
  approval?: { version: number; at: string; by: string }
  invalidated?: { version: number; at: string; reason: string }
  changeRequest?: { version: number; note: string; at: string }
  /** Accuracy evaluation of a specific version (rule checks + judge rubric). Recomputed for every new version. */
  evaluation?: SowEvaluation
}

export interface SowMetric {
  key: string
  label: string
  /** 0–100. */
  score: number
  /** rule: deterministic check on the SOW text; judge: LLM-as-judge rubric (G-Eval style). */
  method: 'rule' | 'judge'
  detail: string
}
export interface SowFinding { requirement: number; passageId: string; field?: string; text: string }
export interface SowEvaluation {
  version: number
  status: 'running' | 'done'
  at: string
  /** The judge model / rubric used for the judge metrics. */
  judge: string
  /** True while the judge is simulated by the mock backend (no judge model connected). */
  simulated: boolean
  /** Why the judge is simulated (e.g. the Laya service is not running), or model details when it is real. */
  judgeNote?: string
  overall?: number
  metrics: SowMetric[]
  findings: SowFinding[]
}

export const ISATVON = [
  { key: 'instructions', letter: 'I', label: 'Instructions' },
  { key: 'sourceData', letter: 'S', label: 'Source data' },
  { key: 'automation', letter: 'A', label: 'Automation' },
  { key: 'techStack', letter: 'T', label: 'Tech stack' },
  { key: 'variables', letter: 'V', label: 'Variables' },
  { key: 'outcome', letter: 'O', label: 'Outcome' },
  // Display label only: the serialized key stays `notifications` so saved contracts keep loading.
  { key: 'notifications', letter: 'N', label: 'Next' },
] as const

export type IsatvonField = (typeof ISATVON)[number]['key']
export type SkillStatus = 'PARSED' | 'MANIFEST_READY' | 'TEST_PASSED' | 'TEST_FAILED' | 'DEPLOYED'

export interface SkillRow {
  id: string
  agent: string
  name: string
  passageId: string
  passageExcerpt: string
  fields: Record<IsatvonField, string>
  original: Record<IsatvonField, string>
  errors: Partial<Record<IsatvonField, string>>
  status: SkillStatus
}

export interface Finding {
  id: string
  severity: 'error' | 'warning'
  message: string
  skillId?: string
  file?: string
  line?: number
  resolved?: boolean
}

export interface Activity {
  key: 'A' | 'B' | 'C' | 'D'
  label: string
  status: RunStatus
  attempts: number
  startedAt?: string
  endedAt?: string
  reason?: string
  detail?: string
  findings: Finding[]
  /** Every finished attempt, oldest first; failed attempts stay inspectable after a recovery. */
  history: AttemptRecord[]
}

export interface AttemptRecord {
  attempt: number
  status: RunStatus
  startedAt?: string
  endedAt?: string
  reason?: string
  findings: number
}

export interface Workflow {
  workflowId: string
  runId: string
  status: RunStatus
  startedAt: string
  endedAt?: string
  activities: Activity[]
  manifest?: Manifest
  /** The manifest as the run produced it (JSON text), for the human-edit comparison. */
  generatedManifest?: string
  /** Human approval of the manifest and code; valid only for this manifest hash. Completes 4.1. */
  approval?: { at: string; by: string; hash: string }
}

export interface Manifest {
  version: string
  hash: string
  createdAt: string
  sowVersion: number
  skills: { id: string; agent: string; name: string; file: string }[]
}

export interface CodeFile {
  path: string
  skillId: string
  content: string
  /** What the template produced before any human edit, for the comparison view. */
  generated: string
  edited?: boolean
}

export interface Assertion {
  id: string
  skillId: string
  name: string
  status: 'pass' | 'fail'
  message?: string
  /** Evidence for the assertion (simulated by the mock backend). */
  fixture: string
  input: string
  expected: string
  observed: string
  /** Reference to the stored evidence for this assertion. */
  evidence: string
}

export interface Tests {
  runId: string
  manifestVersion: string
  /** The exact manifest (and code) these results were produced for; results never certify another hash. */
  manifestHash: string
  sowVersion: number
  startedAt: string
  sandbox: Stage
  promptfoo: Stage
  assertions: Assertion[]
  /** Human review of a clean run; required before persist/deploy. */
  reviewedAt?: string
}

export interface VersionRef {
  version: string
  hash: string
  at: string
  runId?: string
}

/** One entry in the manifest's version history. */
export interface VersionEntry extends VersionRef {
  kind: 'draft' | 'certified' | 'frozen' | 'deployed'
  note: string
  /** The manifest JSON at that point, for comparisons. */
  manifest: string
}

export interface Persist {
  draft?: VersionRef
  artifacts: Stage[]
  committed?: VersionRef
  deploy?: Stage
  deployed?: VersionRef & { environment: 'Staging' }
  deployedSkills: { agent: string; skills: string[] }[]
}

export interface Snapshot {
  serverTime: string
  workflowName: string
  activeStep: StepId
  steps: StepInfo[]
  uploads: UploadRecord[]
  /** Scan activity for the active batch, oldest first; ids are stable across polls. */
  scanEvents: ScanEvent[]
  ingestion?: IngestionRun
  sowGeneration: Stage[]
  sow?: Sow
  extraction: Stage[]
  skillsRevision: number
  gridReviewedAt?: string
  workflow?: Workflow
  tests?: Tests
  /** Earlier test runs, newest first; kept as history and never used to certify the current manifest. */
  testHistory: Tests[]
  /** Draft, certified, frozen and deployed manifest versions, oldest first. */
  versions: VersionEntry[]
  persist: Persist
}

export const AUTOMATIONS = [
  'UNSTRUCTURED_TO_SOW_LLM',
  'SOW_TO_EXCEL_MCP',
  'EXCEL_TO_JSON_AST',
  'JSON_TO_PYTHON_JINJA2',
  'HITL_APPROVAL',
  'CAD_STEP_TOLERANCE_EXTRACTOR',
  'PCBA_COSTING_ENGINE',
  'ISATVON_LOOPY_ROUTER',
  'DYNAMIC_CONTEXT_VERIFIER',
] as const

export const ACCEPTED_TYPES = ['.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.png', '.zip']
/** One intake batch: at most 25 files and 250 MB in total. A ZIP counts as one file. */
export const MAX_BATCH_FILES = 25
export const MAX_BATCH_BYTES = 250 * 1024 * 1024
/** Files above this are sent in parts of this size (multipart upload). */
export const CHUNK_BYTES = 10 * 1024 * 1024
/** A single file may use the whole batch budget; anything over 10 MB is chunked. */
export const MAX_UPLOAD_BYTES = MAX_BATCH_BYTES
export const chunkCount = (size: number) => Math.max(1, Math.ceil(size / CHUNK_BYTES))

/** BuLL*Sav = Digibull Security AntiVirus Engine, the 1.1 scan gate. The scan engine behind it is ClamAV (clamd) in production. */
export const AV_NAME = 'BuLL*Sav'
export const AV_VENDOR = 'Digibull Security AntiVirus Engine (ClamAV Core)'

/** Repel = Behavioral Payload & Heuristic Evasion Testing Engine running alongside ClamAV. */
export const REPEL_NAME = 'Repel Evasion Shield'
export const REPEL_VENDOR = 'Repel Behavioral Payload & Prompt Defense'
