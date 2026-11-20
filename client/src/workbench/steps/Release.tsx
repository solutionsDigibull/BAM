import type { CommitStatus, StagingStatus, WorkflowVersion } from '@/contracts'
import type { StepProps } from '../Workbench'
import { Banner, Copy, Empty, ErrorState, GateButton, Kpis, Pane, Section, Skeleton, StatusPill, Time, shortHash } from '../components/ui'
import { goScreen } from '../lib/route'
import { setUi } from '../lib/store'
import { projectIdOfPath } from '../lib/evidence'
import {
  CERTIFICATION_CHECKS, certificationChecks, isCertified, isForbidden, isNotFound, problemOf, useCommitStatus, useWorkflowVersions,
} from '../lib/workflowCommit'
import {
  HEALTH_LABEL, STAGING_STATE_LABEL, healthTone, isNotAvailable, shortDigest, stagingStateTone, useStagingStatus,
} from '../lib/staging'
import { CertEvidence } from './CertEvidence'
import { Checks, Entry, Summary, ValidationFooter } from './Validation'
import './validation.css'

// Certify (4.5), Commit (4.6) and Staging complete (4.7) (#824, ADR 0032). Everything shown is read from the backend:
// the commit status (`GET W/commit`: checks, registration, Staging start), the certificates (#842), the frozen
// versions (`GET W/versions`, #132) and the Staging deployment (`GET W/staging`, #861).
// * Certification is automatic and all-or-nothing: Certify shows it and has no approve action of its own.
// * Commit is the FDE's one approval: the same command as Validation (4.9). It freezes the version and starts Staging.
// * Staging complete confirms what Staging runs and opens Staging mode (the orange toggle). Staging is not production.

const projectId = () => (typeof window === 'undefined' ? undefined : projectIdOfPath(window.location.pathname))

/** The shared states of a screen that reads the commit status: no workflow, forbidden, not found, error, loading. */
function useStatusBody(workflowId: string | undefined, what: string) {
  const { data, error, isLoading, refetch } = useCommitStatus(workflowId)
  let fallback: React.ReactNode = null
  if (!workflowId) fallback = <Empty icon="file" title={`Nothing to ${what} yet`}>This opens once the build has produced a workflow.</Empty>
  else if (error) {
    fallback = isForbidden(error) ? <ErrorState title={`You can't ${what} this workflow`}>Your account is not assigned to this project.</ErrorState>
      : isNotFound(error) ? <Empty icon="file" title="This workflow was not found">It may belong to another project.</Empty>
        : <ErrorState title="Could not load the status" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  } else if (isLoading || !data) fallback = <Skeleton rows={5} label="Loading the status" />
  return { data, fallback }
}

const FootIn = ({ title, line, children }: { title: string; line?: string; children?: React.ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>{children}
  </div>
)

// ---- 4.5 Certify ------------------------------------------------------------------------------------------------

function CertifiedBanner({ s }: { s: CommitStatus }) {
  if (isCertified(s)) return <Banner tone="ok" icon="check"><strong>Certified.</strong> The workflow and every Sub-Skill are certified on their current code. Commit is your approval.</Banner>
  const reported = certificationChecks(s).length
  if (reported < CERTIFICATION_CHECKS.length) return <Banner tone="info" icon="refresh"><strong>Certification has not finished.</strong> It runs by itself after the tests; this screen shows it when it ends.</Banner>
  return <Banner tone="warn" icon="lock"><strong>Not certified.</strong> A check below did not pass. Certification is automatic: fix the build and it runs again.</Banner>
}

export function CertifyCenter({ snap }: StepProps) {
  const { data, fallback } = useStatusBody(snap.workflowId, 'certify')
  const pid = projectId()
  return (
    <Pane id="center-title" kicker="Automatic certification" title="Certify" className="center"
      actions={data?.manifest_hash ? <span className="mono xs muted">manifest {shortHash(data.manifest_hash.replace(/^sha256:/, ''))}</span> : undefined}>
      <div className="val">{fallback ?? <>
        <CertifiedBanner s={data!} />
        <Section title="Certification checks"><Checks s={data!} only={CERTIFICATION_CHECKS} /></Section>
        {pid && snap.workflowId && <CertEvidence projectId={pid} workflowId={snap.workflowId} />}
      </>}</div>
    </Pane>
  )
}

/** No approve action: certification is automatic (ADR 0032). The one action opens Commit once it is certified. */
export function CertifyFooter({ snap }: StepProps) {
  const { data } = useCommitStatus(snap.workflowId)
  if (!snap.workflowId) return null
  const ok = isCertified(data)
  return <FootIn title={ok ? 'Certified' : 'Waiting for certification'} line={ok ? 'Commit is the approval that freezes this version and starts Staging' : 'Certification runs by itself; nothing to approve here'}>
    <GateButton icon="tb-database-import" reason={ok ? undefined : 'Available once the workflow is certified'} onClick={() => goScreen('4.6')}>Open Commit</GateButton>
  </FootIn>
}

// ---- 4.6 Commit -------------------------------------------------------------------------------------------------

function Versions({ workflowId }: { workflowId: string }) {
  const { data, error, isLoading, refetch } = useWorkflowVersions(workflowId)
  let body
  if (error) body = isForbidden(error) || isNotFound(error) ? <p className="muted sm">The frozen versions are not available to you.</p>
    : <ErrorState title="Could not load the frozen versions" action={<button className="btn btn-secondary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  else if (isLoading || !data) body = <Skeleton rows={2} label="Loading the frozen versions" />
  else if (data.items.length === 0) body = <Empty icon="lock" title="Nothing frozen yet">Commit freezes this version. A frozen version never changes again.</Empty>
  else body = (
    <table className="table" aria-label="Frozen versions">
      <thead><tr><th scope="col">Version</th><th scope="col">Frozen</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
      <tbody>{data.items.map((v: WorkflowVersion) => (
        <tr key={v.id}>
          <td className="mono">{v.version}</td>
          <td><Time iso={v.frozen_at} /></td>
          <td><StatusPill status={v.superseded ? 'pending' : 'pass'} text={v.superseded ? 'Superseded' : 'Current'} /></td>
          <td className="muted xs">{v.change_reason || '—'}</td>
        </tr>
      ))}</tbody>
    </table>
  )
  return <Section title="Frozen versions">{body}</Section>
}

export function CommitCenter({ snap }: StepProps) {
  const { data, fallback } = useStatusBody(snap.workflowId, 'commit')
  return (
    <Pane id="center-title" kicker="Your approval" title="Commit" className="center"
      actions={data?.manifest_hash ? <span className="mono xs muted">manifest {shortHash(data.manifest_hash.replace(/^sha256:/, ''))}</span> : undefined}>
      <div className="val">{fallback ?? <>
        <Summary s={data!} />
        <Section title="Commit package">
          <ul className="muted sm">
            <li>Freezes this version: it never changes again (a change is a new PATCH version).</li>
            <li>Registers it in Staging on the image that was built and certified. Nothing is rebuilt.</li>
            <li>Starts it in Staging. Staging is not production; clients cannot see it.</li>
          </ul>
        </Section>
        <Versions workflowId={snap.workflowId!} />
        <Entry s={data!} />
        {data!.state === 'COMMITTED' && <p><button className="btn btn-secondary" onClick={() => goScreen('4.7')}>Staging status</button></p>}
      </>}</div>
    </Pane>
  )
}

/** The same Commit as Validation (4.9): one command, one Idempotency-Key per hash; 409 and 423 come back as problems. */
export const CommitFooter = ValidationFooter

// ---- 4.7 Staging complete ----------------------------------------------------------------------------------------

const kpiTone = (t: 'ok' | 'warn' | 'err' | 'muted') => (t === 'muted' ? undefined : t)

function Deployment({ s }: { s: StagingStatus }) {
  const d = s.current
  return <>
    <Kpis label="Staging summary" items={[
      { value: STAGING_STATE_LABEL[s.state], label: 'State', tone: kpiTone(stagingStateTone(s.state)) },
      { value: HEALTH_LABEL[s.health], label: 'Health', tone: kpiTone(healthTone(s.health)) },
      { value: d?.version ?? '—', label: 'Version on Staging' },
      { value: 'Staging', label: 'Environment (not production)' },
    ]} />
    {d && <dl className="kv" aria-label="Staging deployment">
      <div><dt>Registered as</dt><dd className="mono">{d.registry_name}</dd></div>
      <div><dt>Image digest</dt><dd><Copy value={d.image_digest} display={shortDigest(d.image_digest)} label="image digest" /></dd></div>
    </dl>}
  </>
}

export function StagingCompleteCenter({ snap }: StepProps) {
  const { data, error, isLoading, refetch } = useStagingStatus(snap.workflowId)
  let body
  if (!snap.workflowId) body = <Empty icon="rocket" title="Nothing on Staging yet">Commit starts the workflow in Staging.</Empty>
  else if (error) body = isForbidden(error) ? <ErrorState title="You can't see this Staging deployment">Your account is not assigned to this project.</ErrorState>
    : isNotAvailable(error) ? <Empty icon="rocket" title="Nothing on Staging yet">Commit starts the workflow in Staging. Until then, Staging has nothing from this workflow.</Empty>
      : <ErrorState title="Could not load the Staging status" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  else if (isLoading || !data) body = <Skeleton rows={4} label="Loading the Staging status" />
  else body = <>
    {data.state === 'RUNNING' ? <Banner tone="ok" icon="check"><strong>Staging complete.</strong> The committed workflow is running in Staging. Staging is not production.</Banner>
      : data.state === 'PENDING_RUNTIME' ? <Banner tone="warn" icon="refresh"><strong>Waiting for the Staging runtime.</strong> The workflow is frozen and registered; it starts when the runtime answers.</Banner>
        : data.state === 'FAILED' ? <Banner tone="err" icon="x"><strong>Staging did not start.</strong> Commit again from Commit to start it; nothing is rebuilt.</Banner>
          : <Banner tone="info" icon="info">{STAGING_STATE_LABEL[data.state]}.</Banner>}
    <Deployment s={data} />
  </>
  return (
    <Pane id="center-title" kicker="Commit · Staging status" title="Staging complete" className="center">
      <div className="val">{body}</div>
    </Pane>
  )
}

/** Opens Staging mode (the orange toggle) once the workflow runs there; FDE mode never shows the workflow's UI. */
export function StagingCompleteFooter({ snap }: StepProps) {
  const { data } = useStagingStatus(snap.workflowId)
  if (!snap.workflowId) return null
  const running = data?.state === 'RUNNING'
  return <FootIn title={running ? `Live on Staging · ${data?.current?.version ?? ''}` : 'Not running in Staging'} line="Staging is not production">
    <GateButton icon="rocket" reason={running ? undefined : 'Available once the workflow runs in Staging'} onClick={() => setUi({ env: 'Staging' })}>Open Staging</GateButton>
  </FootIn>
}
