import { useState, type FormEvent } from 'react'
import type { RegisteredWorkflow } from '@/contracts'
import {
  isForbidden, isNotRegistered, isSuperseded, isValidVersion, KIND_LABEL, kindTone, shortDigest, STATUS_LABEL, statusTone, toolCounts,
  useWorkflowRegistration,
} from '../lib/workflowRegistry'
import { Banner, Copy, Empty, ErrorState, Section, Skeleton, Time } from './ui'
import './registryPanel.css'

// Translate (4.3), the registry entry of the workflow, read only (#849): the name it is registered under in Bifrost,
// its version, the image digest and manifest hash that were certified, the certification, and whether a later version
// superseded it (#760). A workflow with no entry is "not registered yet", never an error. Nothing here registers,
// enables or disables anything: registering is the Commit step's job (#766).

function Forbidden() {
  return <ErrorState title="You can't see this workflow's registry entry">Your account is not assigned to this project.</ErrorState>
}

function Entry({ r, open }: { r: RegisteredWorkflow; open: (version: string) => void }) {
  const counts = toolCounts(r.tools)
  return (
    <section className="reg-card" aria-label={`Registry entry ${r.registry_name}`}>
      <header className="reg-head">
        <h4>{r.registry_name}</h4>
        <span className={`pill tone-${kindTone(r.kind)}`}>{KIND_LABEL[r.kind]}</span>
        <span className={`pill tone-${statusTone(r.status)}`}>{STATUS_LABEL[r.status]}</span>
        <span className="mono xs">v{r.version}</span>
      </header>
      {isSuperseded(r) && (
        <Banner tone="warn" icon="alert"
          action={r.superseded_by ? <button className="btn btn-secondary sm" onClick={() => open(r.superseded_by!)}>Show v{r.superseded_by}</button> : undefined}>
          Superseded{r.superseded_at && <> <Time iso={r.superseded_at} /></>}{r.superseded_by && <> by version {r.superseded_by}</>}. It is
          disabled in Bifrost but stays listed, so a running SRIM can still look this version up.
        </Banner>
      )}
      <dl className="kv">
        <div><dt>Kind</dt><dd>{KIND_LABEL[r.kind]}{r.kind === 'staging' && <span className="muted xs"> (the assigned FDE only; SRIM cannot see it)</span>}</dd></div>
        <div><dt>Version</dt><dd className="mono">{r.version}</dd></div>
        <div><dt>Registered</dt><dd><Time iso={r.registered_at} /></dd></div>
        <div><dt>Registered by</dt><dd>{r.owner}</dd></div>
        <div><dt>Registry ID</dt><dd><Copy value={r.registry_id} label="registry ID" /></dd></div>
        <div><dt>Image digest</dt><dd><Copy value={r.image_digest} display={shortDigest(r.image_digest)} label="image digest" /></dd></div>
        <div><dt>Manifest hash</dt><dd><Copy value={r.manifest_hash} display={shortDigest(r.manifest_hash)} label="manifest hash" /></dd></div>
        <div><dt>Certification</dt><dd><Copy value={r.certification_id} label="certification ID" /></dd></div>
      </dl>
      <Section title={`Tools (${r.tools.length})`}>
        <p className="muted xs">{counts.action} action and {counts.check} check {r.tools.length === 1 ? 'tool' : 'tools'}, each with its input and output schema.</p>
        <ul className="reg-tools" aria-label="Tools">
          {r.tools.map(t => (
            <li key={t.name}><code>{t.name}</code> <span className="pill tone-muted">{t.role}</span> <span className="muted xs">{t.skill_id}</span></li>
          ))}
        </ul>
      </Section>
    </section>
  )
}

/** The panel body: loading, error, forbidden, not registered and the entry. */
export function RegistryPanel({ workflowId }: { workflowId?: string }) {
  const [version, setVersion] = useState<string | undefined>()
  const [draft, setDraft] = useState('')
  const { data, error, isLoading, refetch } = useWorkflowRegistration(workflowId, version)
  const open = (v: string) => { setVersion(v); setDraft(v) }
  const current = () => { setVersion(undefined); setDraft('') }
  const submit = (e: FormEvent) => { e.preventDefault(); if (isValidVersion(draft)) setVersion(draft.trim()) }

  if (!workflowId) return <Empty icon="file" title="No registry entry yet">The registry entry appears once the build has started.</Empty>

  const lookup = (
    <form className="reg-lookup" onSubmit={submit} aria-label="Look up a version">
      <label className="xs muted" htmlFor="reg-version">Version</label>
      <input id="reg-version" className="reg-input" value={draft} onChange={e => setDraft(e.target.value)} placeholder="1.0.2" inputMode="numeric"
        aria-invalid={draft !== '' && !isValidVersion(draft)} />
      <button className="btn btn-secondary sm" type="submit" disabled={!isValidVersion(draft)}>Look up</button>
      {version && <button className="btn btn-secondary sm" type="button" onClick={current}>Show the current version</button>}
      {draft !== '' && !isValidVersion(draft) && <span className="xs muted" role="status">Use the form MAJOR.0.PATCH, for example 1.0.2.</span>}
    </form>
  )

  let body
  if (error) {
    if (isForbidden(error)) return <Forbidden />
    body = isNotRegistered(error)
      ? (
        <Empty icon="file" title={version ? `Version ${version} is not registered` : 'Not registered yet'}>
          A workflow is registered in Bifrost after it is certified and committed. Until then SRIM cannot see it.
        </Empty>
      )
      : (
        <ErrorState title="Could not load the registry entry" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>
          {(error as { problem?: { detail?: string } }).problem?.detail ?? (error as { message?: string }).message}
        </ErrorState>
      )
  } else if (isLoading || !data) {
    body = <Skeleton rows={6} label="Loading the registry entry" />
  } else {
    body = <Entry r={data} open={open} />
  }

  return (
    <div className="reg" aria-label="Registry">
      {lookup}
      {body}
      <p className="reg-foot muted xs">Read only. A workflow is registered when it is committed (the Commit step); SRIM looks it up by workflow ID and version.</p>
    </div>
  )
}
