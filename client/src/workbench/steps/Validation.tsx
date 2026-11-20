import { useEffect, useRef, useState } from 'react'
import type { CommitStatus } from '@/contracts'
import { api } from '../api/adapter'
import type { StepProps } from '../Workbench'
import { ConnectionsPanel } from '../components/ConnectionsPanel'
import { Banner, Confirm, Copy, Empty, ErrorState, GateButton, Icon, Pane, Section, Skeleton, StatusPill, Time, shortHash } from '../components/ui'
import { useAction } from '../lib/data'
import { setUi } from '../lib/store'
import { useConnections, tailText } from '../lib/connections'
import { projectIdOfPath } from '../lib/evidence'
import {
  canRestart, checkLabel, commitReason, isForbidden, isNotFound, isRunning, problemOf, STAGING_LABEL, useCommitStatus,
} from '../lib/workflowCommit'
import { idempotencyKey } from '../http/http'
import { CertEvidence } from './CertEvidence'
import './validation.css'

// Validation (4.9, #766, ADR 0032): FDE mode builds and checks, it does not run the workflow. This screen shows the
// automated checks and the certification evidence read only, the project's secrets with the controls to set and test
// them, and the Commit that freezes the workflow, registers it in Staging on the digest that was certified (no rebuild)
// and asks the Staging runtime to start it. The workbench's switch to the Staging toggle is #861, not this screen.

export function Checks({ s, only }: { s: CommitStatus; only?: readonly string[] }) {
  const checks = (s.checks ?? []).filter(c => !only || only.includes(c.name))
  return (
    <ol className="cert-list" aria-label="Automated checks">
      {checks.map(c => (
        <li key={c.name} className={`cert-item cs-${c.passed ? 'pass' : 'fail'}`} data-check={c.name}>
          <StatusPill status={c.passed ? 'pass' : 'fail'} text={c.passed ? 'Pass' : 'Not passed'} />
          <div className="cert-main">
            <span className="cert-label">{checkLabel(c.name)}</span>
            {c.reason && <span className="cert-detail">{c.reason}</span>}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function Entry({ s }: { s: CommitStatus }) {
  const r = s.registration
  if (!r) return null
  return (
    <Section title="Staging entry">
      <dl className="kv" aria-label="Staging entry">
        <div><dt>Registered as</dt><dd className="mono">{r.registry_name}</dd></div>
        <div><dt>Version</dt><dd className="mono">{r.version}</dd></div>
        <div><dt>Image digest</dt><dd><Copy value={r.image_digest} display={shortHash(r.image_digest.replace(/^sha256:/, ''))} label="image digest" /> <span className="muted xs">the digest that was certified</span></dd></div>
        <div><dt>Manifest hash</dt><dd><Copy value={r.manifest_hash} display={shortHash(r.manifest_hash.replace(/^sha256:/, ''))} label="manifest hash" /></dd></div>
        {s.committed_by && <div><dt>Approved by</dt><dd>{s.committed_by}{s.committed_at && <> <Time iso={s.committed_at} /></>}</dd></div>}
      </dl>
    </Section>
  )
}

export function Summary({ s }: { s: CommitStatus }) {
  if (s.state === 'COMMITTED') {
    const staging = s.staging
    return (
      <Banner tone={staging?.status === 'FAILED' ? 'err' : 'ok'} icon={staging?.status === 'FAILED' ? 'x' : 'check'}>
        <strong>Committed to Staging{s.registration ? ` as version ${s.registration.version}` : ''}.</strong>{' '}
        {staging ? <>{STAGING_LABEL[staging.status]}{staging.detail ? `: ${staging.detail}` : ''}.</> : 'Waiting for the Staging runtime to answer.'}
        {staging?.status === 'PENDING_RUNTIME' && ' The workflow is frozen and registered; Commit again to start it once the runtime is available.'}
      </Banner>
    )
  }
  if (s.state === 'COMMITTING') {
    return isRunning(s)
      ? <Banner tone="info" icon="refresh">Committing: {s.run?.current_stage ?? 'starting'}. This screen updates by itself.</Banner>
      : <Banner tone="warn" icon="alert"><strong>A commit started but did not finish.</strong> {s.run?.last_error ?? 'It stopped before the end.'} Commit again to complete it; every step picks up where the last one stopped.</Banner>
  }
  const why = commitReason(s)
  return why
    ? <Banner tone="warn" icon="lock"><strong>Commit is blocked.</strong> {why}</Banner>
    : <Banner tone="ok" icon="check"><strong>Every check passes.</strong> Review the secrets, then Commit.</Banner>
}

/** The screen's body: loading, error, forbidden, nothing built yet, and the checks, evidence and secrets. */
export function ValidationCenter({ snap }: StepProps) {
  const workflowId = snap.workflowId
  const projectId = typeof window === 'undefined' ? undefined : projectIdOfPath(window.location.pathname)
  const { data, error, isLoading, refetch } = useCommitStatus(workflowId)
  let body
  if (!workflowId) body = <Empty icon="file" title="Nothing to validate yet">Validation opens once the build has produced a workflow.</Empty>
  else if (error) {
    body = isForbidden(error) ? <ErrorState title="You can't validate this workflow">Your account is not assigned to this project.</ErrorState>
      : isNotFound(error) ? <Empty icon="file" title="This workflow was not found">It may belong to another project.</Empty>
        : <ErrorState title="Could not load the checks" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  } else if (isLoading || !data) body = <Skeleton rows={6} label="Loading the checks" />
  else {
    body = <>
      <Summary s={data} />
      <Section title="Automated checks"><Checks s={data} /></Section>
      {projectId && <CertEvidence projectId={projectId} workflowId={workflowId} />}
      <Section title="Secrets"><ConnectionsPanel readOnly={data.state === 'COMMITTED'} /></Section>
      <Entry s={data} />
    </>
  }
  return (
    <Pane id="center-title" kicker="Validate and commit" title="Validation" className="center"
      actions={data?.manifest_hash ? <span className="mono xs muted">manifest {shortHash(data.manifest_hash.replace(/^sha256:/, ''))}</span> : undefined}>
      <div className="val">{body}</div>
    </Pane>
  )
}

/** The one primary action: Commit. It asks first and lists every secret masked (the same keys go to Staging). */
export function ValidationFooter({ snap, info }: { snap: StepProps['snap']; info: StepProps['info'] }) {
  const workflowId = snap.workflowId
  const { data } = useCommitStatus(workflowId)
  const { data: secrets } = useConnections()
  const { busy, run } = useAction()
  const [ask, setAsk] = useState(false)
  const [failed, setFailed] = useState<string | undefined>()
  const key = useRef<{ hash: string; key: string } | undefined>(undefined)
  // Commit leads to Staging in the same login (#861, ADR 0032): once this session's Commit is recorded, the toggle flips to Staging.
  const flip = useRef(false)
  useEffect(() => {
    if (flip.current && data?.state === 'COMMITTED') { flip.current = false; setUi({ env: 'Staging' }) }
  }, [data?.state])
  const ro = info.editable ? undefined : 'This screen is read-only'
  if (!workflowId) return null

  const reason = ro ?? commitReason(data)
  const again = (data?.state === 'COMMITTING' && !isRunning(data)) || canRestart(data)
  const send = async () => {
    const hash = data?.manifest_hash
    if (!hash) return
    if (key.current?.hash !== hash || canRestart(data)) key.current = { hash, key: idempotencyKey() } // a retry of the same hash is the same command
    setAsk(false)
    setFailed(undefined)
    await run('Commit', async () => {
      try { await api.commitWorkflow(workflowId, hash, key.current!.key); if (canRestart(data)) setUi({ env: 'Staging' }); else flip.current = true } catch (e) { setFailed(problemOf(e)) }
    })
  }
  const working = busy === 'Commit' || isRunning(data)
  const done = data?.state === 'COMMITTED' && !canRestart(data)
  const title = done ? 'Committed to Staging' : failed ? 'The commit did not start' : again ? 'The last commit did not finish' : 'Commit to Staging'
  const line = failed ?? (done ? 'Staging is not production' : 'Freezes this version and registers it in Staging on the digest that was certified')

  return (
    <div className="ws-foot-in" role="status" aria-live="polite">
      <div className="ws-foot-text"><strong>{title}</strong><span className="muted xs">{line}</span></div>
      <GateButton action="commit" reason={done ? undefined : reason} busy={working} onClick={() => setAsk(true)}>{done ? 'Committed' : again ? 'Commit again' : 'Commit'}</GateButton>
      <Confirm open={ask} title="Commit to Staging?" confirmLabel="Commit" onClose={() => setAsk(false)} onConfirm={() => void send()}
        consequences={[
          'The workflow is frozen: this version never changes again.',
          'It is registered in Staging on the image that was built and certified. Nothing is rebuilt.',
          'Only you can reach Staging; clients cannot see it.',
        ]}>
        <p className="muted xs">The secrets Staging starts with (values stay hidden):</p>
        <ul className="val-secrets" aria-label="Secrets in this commit">
          {(secrets?.connections ?? []).length === 0
            ? <li className="muted xs">None are needed.</li>
            : (secrets?.connections ?? []).map(c => <li key={c.name}><Icon name="lock" size={12} /> <code>{c.name}</code> <span className="muted xs">{c.status === 'MISSING' ? 'not set' : tailText(c)}</span></li>)}
        </ul>
      </Confirm>
    </div>
  )
}
