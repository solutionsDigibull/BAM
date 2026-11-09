// Tracker status from real workflow evidence (backend snapshot + RAG pipeline). Pure, so it is unit tested.
// Completion follows saved reviews, checks and gates, never a route visit. Stage 1 completes from the intake
// gates alone (upload, BuLL*Sav verdicts, quarantine, hand-off to RAG), so its screens finish automatically.
import type { Snapshot, StepState } from '../api/types'
import { templateApproved, type StepApprovals } from '../lib/approvals'
import type { RagState } from '../rag/ragPipeline'
import type { TrackerStatus } from './trackerConfig'
import { currentTests } from '../lib/certification'

const running = (x?: { status: string }) => x?.status === 'running' || x?.status === 'queued'
const FROM_STEP: Record<StepState, TrackerStatus> = {
  locked: 'not_started', active: 'in_progress', needs_review: 'needs_review', done: 'done', failed: 'blocked',
}

function ragStep(rag: RagState, key: 'chunk' | 'extract' | 'graph'): TrackerStatus {
  if (!rag.runId) return 'not_started'
  const steps = rag.steps
  const i = steps.findIndex(s => s.key === key)
  if (steps[i]!.status === 'done') return 'done'
  if (steps.slice(0, i + 1).some(s => s.status === 'failed')) return 'blocked'
  return 'in_progress'
}

export function evidenceStatuses(snap: Snapshot, rag: RagState, approvals: StepApprovals = {}): Record<string, TrackerStatus> {
  const sow = snap.sow
  const gen = snap.sowGeneration
  const graph = ragStep(rag, 'graph')
  const uploads = snap.uploads.length
  const scanning = snap.uploads.some(u => u.scan === 'scanning' && !u.scanError)
  const wf = snap.workflow
  const approved = !!wf?.manifest && wf.approval?.hash === wf.manifest.hash
  // Code and manifest review (3.3, 4.2, 4.3) completes only with the human approval for the current hash.
  const review: TrackerStatus = !wf ? 'not_started' : wf.status === 'running' ? 'in_progress' : wf.status === 'failed' ? 'blocked' : approved ? 'done' : 'needs_review'
  const held = snap.uploads.some(u => u.scan === 'blocked')
  const scanFailed = snap.uploads.some(u => u.status === 'failed' || u.scanError)
  const t = currentTests(snap)
  const m = wf?.manifest
  const testing = !!t && [t.sandbox, t.promptfoo].some(x => x.status === 'running' || x.status === 'queued')
  const committed = !!m && snap.persist.committed?.hash === m.hash
  const deployed = !!m && snap.persist.deployed?.hash === m.hash
  const deploying = snap.persist.deploy?.status === 'running' || snap.persist.deploy?.status === 'queued'
  // Human-gated Workbench and Validation (18 Oct 2026): Template, Sandbox and Check are done only with the FDE's Approve.
  const tplOk = templateApproved(approvals, wf ?? undefined)
  const sandboxOk = !!t && !!m && approvals.sandbox === m.hash && t.sandbox.status === 'succeeded'
  const checkOk = !!t && approvals.check === t.runId && t.promptfoo.status === 'succeeded'
  const ev: Record<string, TrackerStatus> = {
    // Upload is file transfer: done once a file is stored. It never means the file is safe (that is Scan).
    '1.1': uploads ? 'done' : 'not_started',
    // Scan: every file has a final verdict with no scan failure; a held file needs review in Quarantine.
    '1.5': !uploads ? 'not_started' : scanFailed ? 'blocked' : scanning ? 'in_progress' : held ? 'needs_review' : 'done',
    // File detail (not listed, not counted): every file has a final verdict and released detail.
    '1.3': !uploads ? 'not_started' : scanning ? 'in_progress' : snap.uploads.some(u => u.scan === 'blocked' || u.scanError) ? 'needs_review' : snap.uploads.every(u => u.released) ? 'done' : 'in_progress',
    // Standardize: done once the standardized batch is handed on (Continue to Parse starts the ingestion run).
    '1.8': !uploads ? 'not_started' : snap.ingestion ? 'done' : 'in_progress',
    // Parse: the extraction of the current run.
    '1.9': !snap.ingestion ? 'not_started' : ragStep(rag, 'extract'),
    // Review: accepted files are parsed and handed to Context; a failed build step blocks it.
    '1.4': !uploads || !snap.ingestion ? 'not_started' : rag.steps.some(s => s.status === 'failed') ? 'blocked' : ragStep(rag, 'extract'),
    // Connectors are optional (manual upload is the only source in this build) and never counted.
    '1.6': 'not_started',
    // Quarantine: clear once no file in the batch is held (held files are removed from the batch, never released).
    '1.7': !uploads ? 'not_started' : scanning ? 'in_progress' : held ? 'needs_review' : 'done',
    // Context follows the knowledge build; schema drafts, context testing and readiness review have no backend.
    '2.5': graph,
    '2.1': graph,
    '2.2': 'not_started',
    '2.3': 'not_started',
    '2.4': 'not_started',
    '3.1': sow?.status === 'approved' ? 'done'
      : gen.some(s => s.status === 'failed') ? 'blocked'
      : sow?.status === 'regenerating' || gen.some(s => s.status === 'running' || s.status === 'queued') ? 'in_progress'
      : sow ? 'needs_review' : 'not_started',
    '3.2': FROM_STEP[snap.steps[1]!.state],
    '3.3': review,
    '3.4': !wf?.manifest ? 'not_started' : approved ? 'done' : 'needs_review',
    '4.1': !wf ? 'not_started' : wf.status === 'running' ? 'in_progress' : wf.status === 'failed' ? 'blocked' : 'done',
    // Template: the check activities' run, then the FDE's Approve of its manifest.
    '4.2': !wf ? 'not_started' : wf.status === 'running' ? 'in_progress' : wf.status === 'failed' ? 'blocked' : tplOk || approved ? 'done' : 'needs_review',
    // Translate: opens for review once Template is approved; done with the approval for the current hash.
    '4.3': approved ? 'done' : !wf || !tplOk ? 'not_started' : wf.status === 'running' ? 'in_progress' : wf.status === 'failed' ? 'blocked' : 'needs_review',
    // Sandbox: the isolated environment for the current hash is up, then approved by the FDE.
    '4.8': !t ? 'not_started' : running(t.sandbox) ? 'in_progress' : t.sandbox.status === 'failed' ? 'blocked' : sandboxOk ? 'done' : 'needs_review',
    // Check: after the sandbox is approved, the results for the current hash are clean and approved by the FDE.
    '4.4': !t || !sandboxOk ? 'not_started' : testing ? 'in_progress' : t.promptfoo.status === 'failed' ? 'blocked' : checkOk ? 'done' : 'needs_review',
    // Certify: the FDE's sign-off on the approved, clean results is recorded for this hash.
    '4.5': t?.reviewedAt && t.promptfoo.status === 'succeeded' ? 'done' : checkOk ? 'needs_review' : 'not_started',
    // Commit: the certified version is saved (frozen) in BAM_db for the current hash.
    '4.6': committed ? 'done' : t?.reviewedAt ? 'needs_review' : 'not_started',
    // Staging complete: the backend reports step 4 done and the current manifest is live on Staging.
    '4.7': snap.steps[3]!.state === 'done' && deployed ? 'done' : deploying ? 'in_progress' : 'not_started',
    // Deploy to Staging lives in Commit's details; it is shown, never counted (4.7 is hidden).
  }
  // Work built on an earlier RAG run is kept but shows as not started (marked "from previous run" by previousRun()).
  for (const id of previousRun(snap, rag)) ev[id] = 'not_started'
  return ev
}

/** FDE Workbench and Sandbox screens whose evidence comes from an earlier RAG run (the SOW was generated from another run's context). */
export function previousRun(snap: Snapshot, rag: RagState): string[] {
  const from = snap.sow?.contextRunId
  return from && rag.runId && from !== rag.runId ? ['3.1', '3.2', '3.3', '3.4', '4.1', '4.2', '4.3', '4.8', '4.4', '4.5', '4.6', '4.7'] : []
}

/** What marks each screen done: the single rule behind the tracker counts, shown in the chip tooltips. */
export const DONE_RULE: Record<string, string> = {
  '1.1': 'Done when a file is stored. The scan starts by itself; upload never marks a file safe.',
  '1.5': 'Done when every file has a final ClamAV and Repel verdict with no scan failure and nothing held.',
  '1.3': 'Done when every file has a final BuLL*Sav verdict and is released from quarantine.',
  '1.8': 'Done once ingestion has been started from here.',
  '1.9': 'Done when extraction of the current run finishes.',
  '1.4': 'Done when the accepted files are parsed and handed to Context.',
  '2.5': 'Done when Start on Sync has run the current context build (graph build).',
  '1.6': 'Optional: not counted. Manual upload is the only source available.',
  '1.7': 'Done when no file in the batch is held by the scan.',
  '2.1': 'Done when the context graph build of the current run finishes.',
  '2.2': 'Done when a schema from the current RAG run is sent to the Workbench.',
  '2.3': 'Done when the verified context of the current run is approved.',
  '2.4': 'Done when a schema from the current RAG run is sent to the Workbench.',
  '3.1': 'Done when the current SOW version is approved.',
  '3.2': 'Done when the grid is reviewed and Start generates the ISATVON Contract.',
  '3.3': 'Done when manifest and code are approved for the current hash (Approve in Translate). Opening Skill review is not required.',
  '3.4': 'Done when manifest and code are approved for the current hash (Approve in Translate). Opening Release prep is not required.',
  '4.1': 'Done when the validation run (activities A to D) succeeds.',
  '4.2': 'Done when manifest and code are approved for the current hash.',
  '4.3': 'Done when manifest and code are approved for the current hash.',
  '4.8': 'Done when the isolated sandbox run for the current manifest finishes.',
  '4.4': 'Done when the validation results for the current manifest are clean.',
  '4.5': 'Done when the FDE certifies (reviews) the clean results for this exact manifest hash.',
  '4.6': 'Done when the certified version is committed (frozen) to BAM_db.',
  '4.7': 'Done when the certified manifest is live on Staging. Staging is not production.',
}
