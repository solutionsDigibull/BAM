// What actually blocks a locked screen, derived from backend + RAG state. The reason text and the
// button target come from the same check, so a lock never says "approve the SOW" while sending you to Upload.
import type { Snapshot, StepId } from '../api/types'
import type { RagState } from '../rag/ragPipeline'

export interface Blocker { reason: string; label: string; target: string }

/** The first unmet prerequisite for working on backend step `step`, or undefined when nothing blocks it. */
export function prerequisite(snap: Snapshot, rag: Pick<RagState, 'runId' | 'steps'>, step: StepId): Blocker | undefined {
  if (!snap.uploads.length) return { reason: 'Source documents are needed first. The SOW is generated from the RAG context of the uploaded documents.', label: 'Upload documents', target: '1.1' }
  if (!snap.ingestion) return { reason: 'Documents are uploaded but processing has not started. Start it in Standardize once every file has been read.', label: 'Open Standardize', target: '1.8' }

  const sow = snap.sow
  const genFailed = snap.sowGeneration.find(s => s.status === 'failed')
  const generating = snap.sowGeneration.some(s => s.status === 'running' || s.status === 'queued')
  if (!sow) {
    if (genFailed) return { reason: `SOW generation failed at "${genFailed.label}": ${genFailed.error ?? 'no reason reported'}`, label: 'Open Scope to retry', target: '3.1' }
    if (generating) return { reason: 'The SOW is being generated from the context. It needs review and approval in Scope when it is ready.', label: 'Open Scope', target: '3.1' }
    const ragFailed = rag.steps.find(s => s.status === 'failed')
    if (ragFailed) return { reason: `The context build failed at "${ragFailed.label}", so no SOW has been generated yet.`, label: 'Open Parse to retry', target: '1.9' }
    return {
      reason: rag.runId ? 'The context is still being built. The SOW is generated automatically when it is ready.' : 'Documents are stored but the context has not been built yet, so no SOW exists.',
      label: rag.runId ? 'Open Index' : 'Open Parse', target: rag.runId ? '2.1' : '1.9',
    }
  }
  if (step === 1) return undefined

  if (sow.status === 'regenerating') return { reason: `SOW v${sow.changeRequest?.version ?? sow.version} is being regenerated with AI. Review and approve the new version in Scope.`, label: 'Open Scope', target: '3.1' }
  if (sow.status !== 'approved' || !sow.approval) return { reason: `SOW v${sow.version} is a draft awaiting approval. Approve it in Scope to unlock the Grid.`, label: 'Open Scope', target: '3.1' }
  if (step === 2) return undefined

  const wf = snap.workflow
  if (!wf) return {
    reason: snap.gridReviewedAt ? 'The grid is reviewed; generate the ISATVON Contract in Grid to produce the manifest.' : 'Review the Grid, then generate the ISATVON Contract to produce the manifest.',
    label: 'Open Grid', target: '3.2',
  }
  if (step === 3) return undefined

  if (wf.status === 'running') return { reason: 'The check activities that write the manifest and translated code are still running.', label: 'Open Template', target: '4.2' }
  if (wf.status === 'failed') return { reason: `Translation failed: ${wf.activities.find(a => a.status === 'failed')?.reason ?? 'see the validation activities'}`, label: 'Open Translate', target: '4.3' }
  if (!wf.manifest || wf.approval?.hash !== wf.manifest.hash) return { reason: 'Review Template and Translate, then Approve in Translate to approve this manifest and its code.', label: 'Open Translate', target: '4.3' }
  return undefined
}
