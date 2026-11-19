import type { DataProvider } from '@refinedev/core'
import type {
  BuildsResponse, ChangeNoteList, CommitAccepted, CommitStatus, Connection, KnowledgeNodeDetail, ConnectionList, ConnectionTestResult, ConnectionUpdate, ConnectionValueResult,
  RegisteredWorkflow, RepoAnswered, RepoAnswerRequest, RepoQuestions, RequestChangeAccepted, RequestChangeCreate, StagingStatus, WorkflowCodeTree, WorkflowFileView, WorkflowRepositories,
} from '@/contracts'
import type { CodeFile, IngestionRun, IsatvonField, SkillRow, Snapshot, StepId, UploadRecord } from './types'

export type ScanOutcome = 'clean' | 'threat' | 'error'

// The only seam between UI and backend. `api` below forwards to the implementation installed with
// `setBamApi` (the SBAM HTTP adapter); until one is installed every call rejects, so a screen shows its
// error state instead of invented data. The workbench ships no mock backend.
export interface BamApi {
  getSnapshot(): Promise<Snapshot>
  getSkills(): Promise<SkillRow[]>
  getCode(): Promise<CodeFile[]>
  /** The whole workflow's code as stored, a tag per file (read only, #763). */
  getWorkflowCode(workflowId: string): Promise<WorkflowCodeTree>
  /** One file of that tree: `group` is a sub-skill id or `workflow`. */
  getWorkflowFile(workflowId: string, group: string, path: string): Promise<WorkflowFileView>
  /** The repositories the workflow starts from: patches, scan report and components (read only, #843). */
  getWorkflowRepositories(workflowId: string): Promise<WorkflowRepositories>
  /** The workflow's registry entry: the newest enabled one, or `version` (read only, #849). Rejects with 404 when none. */
  getWorkflowRegistration(workflowId: string, version?: string): Promise<RegisteredWorkflow>
  /** The checks, the secrets gate and what Commit did for the workflow (#766). `manifest_hash` is the ETag a commit sends back. */
  getCommitStatus(workflowId: string): Promise<CommitStatus>
  /** Commit: the FDE's final approval of exactly `manifestHash` (If-Match), with an Idempotency-Key. 409 when it changed or a check fails. */
  commitWorkflow(workflowId: string, manifestHash: string, key: string): Promise<CommitAccepted>
  /** One entity of a knowledge version with its neighbours and the passages of the documents it came from (read only, #712). */
  getKnowledgeNode(versionId: string, nodeId: string): Promise<KnowledgeNodeDetail>
  /** The workflow's Staging deployment: state, health, the version running and the history (FDE only; 404 = never committed or not yours, #861). */
  getStagingStatus(workflowId: string): Promise<StagingStatus>
  /** The change notes of the workflow's current version and how many of the allowed ones are used (#767). */
  getChangeNotes(workflowId: string): Promise<ChangeNoteList>
  /** Request change from Staging (ADR 0032 decision 5): a plain-words note on one Sub-Skill, with an Idempotency-Key. 409 once the version has its 3 notes. */
  requestChange(workflowId: string, body: RequestChangeCreate, key: string): Promise<RequestChangeAccepted>
  /** The Sub-Skill rows of the workflow's build (the choices of a change note). */
  getWorkflowBuilds(workflowId: string): Promise<BuildsResponse>
  /** The project's secrets: names, status and masked tail, never a value (#764). */
  getConnections(): Promise<ConnectionList>
  /** Sets or replaces one value (`ifMatch` is the entry's row version; none for a name with no row yet). The value is never read back. */
  setConnectionValue(name: string, value: string, ifMatch?: string): Promise<ConnectionValueResult>
  clearConnectionValue(name: string, ifMatch?: string): Promise<void>
  /** Where to test a key, or the FDE's statement that it has no remote service. A change makes the value untested. */
  updateConnection(name: string, body: ConnectionUpdate, ifMatch?: string): Promise<Connection>
  /** A real call to the service the FDE named (SSRF-guarded on the server); only `OK` marks the value tested. */
  testConnection(name: string): Promise<ConnectionTestResult>
  /** The builds of the workflow that wait for the FDE to choose a repository, and the decisions taken (#756). */
  getRepoQuestions(workflowId: string): Promise<RepoQuestions>
  /** The FDE's answer to one waiting build: a repository of the short list, or none (the code is generated). `key` is its Idempotency-Key. */
  answerRepoQuestion(workflowId: string, subSkillId: string, body: RepoAnswerRequest, key: string): Promise<RepoAnswered>
  /** stageUpload: stores the file in private quarantine staging and starts an async virus scan (ClamAV in production). */
  uploadFile(file: { name: string; size: number; type?: string; data: Blob; batchId?: string }, onProgress: (loaded: number, total: number) => void): Promise<UploadRecord>
  /** Removes a file from the active intake batch once the service confirms. A blocked object stays in quarantine (audit). Refused while scanning. */
  removeUpload(id: string): Promise<void>
  /** Takes a file out of the recycle bin. It is scanned and read again. Rejects with the server's reason (410 purged, 409 file limit). */
  restoreUpload(id: string): Promise<void>
  /** Rescans a file whose scan failed (outage, timeout, malformed verdict). */
  retryScan(id: string): Promise<void>
  /** Approved access for a released, Verified file only; refused for scanning, blocked or errored files. */
  getVerifiedFileAccess(id: string): Promise<{ url: string; expiresAt: string }>
  /** Atomically re-checks the whole batch (scanGate) and starts one run for the released files, or only `fileIds` of them
   *  (Standardize hands on the ready files; held ones stay out). Rejects ineligible or repeated starts. */
  startIngestionRun(fileIds?: string[]): Promise<IngestionRun>
  /** Generate (or retry) the SOW from the knowledge of the ingested (verified) uploads. */
  startSowGeneration(contextRunId?: string): Promise<void>
  saveSowDraft(html: string, baseVersion: number): Promise<number>
  approveSow(version: number): Promise<void>
  requestSowChanges(version: number, note: string): Promise<void>
  reopen(step: StepId): Promise<void>
  closeReopen(step: StepId): Promise<void>
  updateSkillField(id: string, field: IsatvonField, value: string): Promise<SkillRow>
  markGridReviewed(): Promise<void>
  generateContract(): Promise<void>
  retryActivity(key: 'A' | 'B' | 'C' | 'D'): Promise<void>
  /** Saves a human edit; resolves true when the file passes activity C's AST check. */
  saveCode(path: string, content: string): Promise<boolean>
  /** Saves an edited manifest (JSON). Version, agent and skill names are editable; hash and createdAt are recomputed. */
  saveManifest(json: string): Promise<void>
  /** Human approval of the current manifest and code; completes 4.1 and unlocks 4.4. */
  approveManifest(): Promise<void>
  runTests(): Promise<void>
  markTestsReviewed(): Promise<void>
  commitToPostgres(): Promise<void>
  /** Staging is started by Commit (#766) and read with `getStagingStatus`; there is no separate deploy action. Kept for the old store API. */
  deployToStaging(): Promise<void>
}

export class NotConnectedError extends Error {
  constructor() {
    super('The workbench is not connected to the SBAM API.')
    this.name = 'NotConnectedError'
  }
}

let installed: BamApi | null = null

/** Installs the implementation every `api.*` call forwards to; `null` disconnects. */
export function setBamApi(next: BamApi | null): void {
  installed = next
}

export const api: BamApi = new Proxy({} as BamApi, {
  get(_target, key: string) {
    return (...args: unknown[]) => {
      const impl = installed as unknown as Record<string, (...a: unknown[]) => unknown> | null
      const fn = impl?.[key]
      return fn ? fn.apply(installed, args) : Promise.reject(new NotConnectedError())
    }
  },
})

// Refine reads go through `custom`; resource CRUD is unused by this workbench.
type Query = Record<string, string | undefined>
const reads: Record<string, (q: Query) => Promise<unknown>> = {
  snapshot: () => api.getSnapshot(),
  skills: async () => ({ rows: await api.getSkills() }),
  code: async () => ({ files: await api.getCode() }),
  'workflow-code': q => api.getWorkflowCode(q.workflowId ?? ''),
  'workflow-file': q => api.getWorkflowFile(q.workflowId ?? '', q.group ?? '', q.path ?? ''),
  'workflow-repos': q => api.getWorkflowRepositories(q.workflowId ?? ''),
  'repo-questions': q => api.getRepoQuestions(q.workflowId ?? ''),
  'workflow-registration': q => api.getWorkflowRegistration(q.workflowId ?? '', q.version),
  'commit-status': q => api.getCommitStatus(q.workflowId ?? ''),
  'staging-status': q => api.getStagingStatus(q.workflowId ?? ''),
  'change-notes': q => api.getChangeNotes(q.workflowId ?? ''),
  'workflow-builds': q => api.getWorkflowBuilds(q.workflowId ?? ''),
  connections: () => api.getConnections(),
  'knowledge-node': q => api.getKnowledgeNode(q.versionId ?? '', q.nodeId ?? ''),
}

const unsupported = () => Promise.reject(new Error('Not used by the BAM workbench'))

export const dataProvider: DataProvider = {
  getApiUrl: () => 'sbam://workbench',
  getList: unsupported,
  getOne: unsupported,
  create: unsupported,
  update: unsupported,
  deleteOne: unsupported,
  custom: async ({ url, query }) => {
    const read = reads[url]
    if (!read) throw new Error(`Unknown read: ${url}`)
    return { data: (await read((query ?? {}) as Query)) as never }
  },
}
