import { AttemptsPanel } from './AttemptsPanel'
import { BuildsPanel } from './BuildsPanel'
import { ManifestsPanel } from './ManifestsPanel'
import { TestsPanel } from './TestsPanel'
import { TimelinePanel } from './TimelinePanel'

// The build evidence views of Translate (4.3, #715), read only: they sit beside Workflow files, Repositories and Registry
// in the view switch. One place lists them, so the switch and both Translate centres stay in step.
export const EVIDENCE_VIEWS = [
  { id: 'builds', label: 'Builds' },
  { id: 'attempts', label: 'Code attempts' },
  { id: 'manifests', label: 'Manifests' },
  { id: 'tests', label: 'Tests' },
  { id: 'timeline', label: 'Timeline' },
] as const
export type EvidenceView = (typeof EVIDENCE_VIEWS)[number]['id']
export const isEvidenceView = (v: string): v is EvidenceView => EVIDENCE_VIEWS.some(e => e.id === v)

export function EvidenceBody({ view, workflowId }: { view: EvidenceView; workflowId?: string }) {
  switch (view) {
    case 'builds': return <BuildsPanel workflowId={workflowId} />
    case 'attempts': return <AttemptsPanel workflowId={workflowId} />
    case 'manifests': return <ManifestsPanel workflowId={workflowId} />
    case 'tests': return <TestsPanel workflowId={workflowId} />
    case 'timeline': return <TimelinePanel workflowId={workflowId} />
  }
}
