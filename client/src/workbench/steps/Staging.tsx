import { useRef, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { StagingDeployment, StagingStatus } from '@/contracts'
import { api } from '../api/adapter'
import { Banner, Copy, Empty, ErrorState, Icon, Pane, Section, Skeleton, Time } from '../components/ui'
import { idempotencyKey } from '../http/http'
import { goScreen } from '../lib/route'
import { useUi } from '../lib/store'
import { projectIdOfPath } from '../lib/evidence'
import { stagingFrameSrc } from '@/lib/staging-ui'
import {
  canShowUi, healthTone, HEALTH_LABEL, isConflict, isForbidden, isNotAvailable, MAX_NOTE_CHARS, problemOf, shortDigest,
  STAGING_STATE_LABEL, stagingStateTone, useChangeNotes, useStagingStatus, useSubSkillChoices,
} from '../lib/staging'
import './staging.css'

// Staging (#861, ADR 0032): after Commit the workbench switches to the orange Staging toggle in the same login and shows the
// workflow's complete generated UI, which the FDE uses to run the workflow. FDE mode shows no workflow UI at all: this
// component renders nothing unless the toggle is on Staging, and the frame exists only here. A problem found in Staging
// goes through Request change (a note), never an edit.

function Strip({ s }: { s: StagingStatus }) {
  const c = s.current
  return (
    <dl className="stg-strip" aria-label="Staging status">
      <div><dt>State</dt><dd><span className={`pill tone-${stagingStateTone(s.state)}`}>{STAGING_STATE_LABEL[s.state]}</span></dd></div>
      <div><dt>Health</dt><dd><span className={`pill tone-${healthTone(s.health)}`}>{HEALTH_LABEL[s.health]}</span></dd></div>
      <div><dt>Version</dt><dd className="mono">{c ? `v${c.version}` : '—'}</dd></div>
      <div><dt>Image digest</dt><dd>{c ? <Copy value={c.image_digest} display={shortDigest(c.image_digest)} label="image digest" /> : '—'}</dd></div>
      <div><dt>Egress hosts</dt><dd>{c?.egress_allowed_hosts?.length ? c.egress_allowed_hosts.map(h => <code key={h} className="stg-chip">{h}</code>) : <span className="muted">none</span>}{c && c.egress_enforced === false && <span className="muted xs"> (not enforced yet)</span>}</dd></div>
      <div><dt>Secrets</dt><dd>{c?.secret_names?.length ? c.secret_names.map(n => <code key={n} className="stg-chip">{n}</code>) : <span className="muted">none</span>}</dd></div>
    </dl>
  )
}

/** The UI of a running deployment, or the reason there is none. The frame is the certified bundle: real calls through the Staging MCP route. */
function Stage({ s, projectId, onRetry }: { s: StagingStatus; projectId: string | undefined; onRetry: () => void }) {
  const c = s.current
  if (canShowUi(s) && projectId) {
    return (
      <>
        {s.health === 'UNREACHABLE' && (
          <Banner tone="err" icon="alert" action={<button className="btn btn-secondary sm" onClick={onRetry}>Check again</button>}>
            <strong>The workflow server does not answer.</strong> Calls from the UI below will fail until it is back.
          </Banner>
        )}
        <iframe className="stg-frame" title="Workflow UI (Staging)" src={stagingFrameSrc(projectId, s.workflow_id)}
          sandbox="allow-scripts allow-same-origin allow-forms" referrerPolicy="no-referrer" />
      </>
    )
  }
  if (s.state === 'PENDING_RUNTIME') return (
    <Banner tone="warn" icon="refresh" action={<button className="btn btn-secondary sm" onClick={onRetry}>Check now</button>}>
      <strong>Waiting for the Staging runtime.</strong> {c?.detail ?? 'The workflow is frozen and registered; it starts once the runtime answers.'} This screen checks again by itself.
      {' '}To start it later, press Commit again on Validation.
    </Banner>
  )
  if (s.state === 'FAILED') return (
    <ErrorState title="Staging could not start the workflow" action={<button className="btn btn-primary" onClick={() => goScreen('4.9', true)}>Go to Validation to Commit again</button>}>
      {c?.detail ?? 'The runtime refused to start it.'}
    </ErrorState>
  )
  if (s.state === 'STOPPED') return (
    <Empty icon="info" title="Staging is stopped" action={<button className="btn btn-secondary" onClick={() => goScreen('4.9', true)}>Go to Validation</button>}>
      {c?.detail ?? 'This deployment was stopped.'} Commit again to start it.
    </Empty>
  )
  if (s.state === 'SUPERSEDED') return (
    <Empty icon="info" title="This version was superseded">
      {c?.superseded_by ? `Version ${c.superseded_by} replaced it. ` : ''}A newer version is committed; its Staging deployment is the one to use.
    </Empty>
  )
  if (s.state === 'RUNNING') return (
    <ErrorState title="The workflow UI is not available" action={<button className="btn btn-secondary" onClick={onRetry}>Retry</button>}>
      The deployment runs, but it has no UI bundle to show.
    </ErrorState>
  )
  return <Empty icon="file" title="Not deployed yet">Commit on the Validation screen starts the workflow in Staging.</Empty>
}

function History({ rows }: { rows: StagingDeployment[] }) {
  if (!rows.length) return <p className="muted xs">No earlier version.</p>
  return (
    <ol className="stg-history" aria-label="Version history">
      {rows.map(d => (
        <li key={`${d.version}-${d.started_at ?? d.state}`}>
          <span className="mono">v{d.version}</span>
          <span className={`pill tone-${stagingStateTone(d.state)}`}>{STAGING_STATE_LABEL[d.state]}</span>
          <span className="muted xs">{d.started_at ? <Time iso={d.started_at} prefix="started " /> : 'never started'}{d.stopped_at && <Time iso={d.stopped_at} prefix=", stopped " />}{d.superseded_by && `, replaced by v${d.superseded_by}`}</span>
          <span className="muted xs mono">{shortDigest(d.image_digest)}</span>
        </li>
      ))}
    </ol>
  )
}

/** Request change (#767): the FDE cannot edit the code; a note on one Sub-Skill, three per version. */
export function RequestChange({ workflowId }: { workflowId: string }) {
  const qc = useQueryClient()
  const { data: notes, error: notesError } = useChangeNotes(workflowId)
  const subs = useSubSkillChoices(workflowId)
  const [sub, setSub] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [failed, setFailed] = useState<{ detail: string; full: boolean } | undefined>()
  const key = useRef<{ note: string; key: string } | undefined>(undefined)
  const used = notes?.used ?? 0, limit = notes?.limit ?? 3
  const full = used >= limit || failed?.full === true
  const chosen = sub || subs[0]?.sub_skill_id || ''
  const note = text.trim()
  const reason = full ? `This version already has ${limit} change notes.` : !chosen ? 'Choose a Sub-Skill' : !note ? 'Write what is wrong' : note.length > MAX_NOTE_CHARS ? `At most ${MAX_NOTE_CHARS} characters` : undefined

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (reason || busy) return
    if (key.current?.note !== `${chosen}:${note}`) key.current = { note: `${chosen}:${note}`, key: idempotencyKey() } // a retry of the same note is the same command
    setBusy(true); setFailed(undefined); setSent(false)
    try {
      await api.requestChange(workflowId, { sub_skill_id: chosen, note }, key.current.key)
      setSent(true); setText(''); key.current = undefined
    } catch (err) {
      setFailed({ detail: problemOf(err), full: isConflict(err) })
    } finally {
      setBusy(false)
      void qc.invalidateQueries()
    }
  }

  return (
    <Section title="Request a change" aside={<span className="muted xs" aria-label="Change notes used">{used} of {limit} used</span>}>
      <form className="stg-change" onSubmit={e => void submit(e)} aria-label="Request a change">
        <p className="muted xs">The code is read only. Describe the problem you found in Staging in plain words; SBAM decides whether the code or the Sub-Skill&apos;s meaning changes.</p>
        <label className="xs muted" htmlFor="stg-sub">Sub-Skill</label>
        <select id="stg-sub" className="reg-input" value={chosen} onChange={e => setSub(e.target.value)} disabled={full || !subs.length}>
          {subs.map(r => <option key={r.sub_skill_id} value={r.sub_skill_id}>{r.skill_name || r.skill_id}</option>)}
        </select>
        <label className="xs muted" htmlFor="stg-note">What is wrong</label>
        <textarea id="stg-note" className="stg-note" rows={3} value={text} maxLength={MAX_NOTE_CHARS} onChange={e => setText(e.target.value)} disabled={full} />
        <div className="stg-change-foot">
          <button className="btn btn-primary sm" type="submit" disabled={!!reason || busy}>{busy ? 'Sending…' : 'Send note'}</button>
          {reason && !busy && <span className="xs muted" role="status">{reason}</span>}
        </div>
        {sent && <Banner tone="ok" icon="check">The note was sent. It is applied to the next build of this version.</Banner>}
        {failed && (
          <Banner tone={failed.full ? 'warn' : 'err'} icon="alert"
            action={failed.full ? <button type="button" className="btn btn-secondary sm" onClick={() => goScreen('3.2', true)}>Open the ISATVON Grid</button> : undefined}>
            {failed.detail}{failed.full && ' Change the Sub-Skill in the ISATVON Grid instead.'}
          </Banner>
        )}
        {notesError && !isNotAvailable(notesError) && <p className="xs muted">The earlier notes could not be loaded.</p>}
        {!!notes?.notes?.length && (
          <ul className="stg-notes" aria-label="Change notes">
            {notes.notes.map(n => <li key={n.id}><span className="pill tone-muted">{n.status.replace('_', ' ').toLowerCase()}</span> {n.note}{n.reason && <span className="muted xs"> ({n.reason})</span>}</li>)}
          </ul>
        )}
      </form>
    </Section>
  )
}

/** The Staging screen: status strip, the generated UI, the history and Request change. Only in Staging mode. */
export function StagingCenter({ workflowId }: { workflowId?: string }) {
  const env = useUi(s => s.env)
  const projectId = typeof window === 'undefined' ? undefined : projectIdOfPath(window.location.pathname)
  const { data, error, isLoading, refetch } = useStagingStatus(workflowId, env === 'Staging')
  if (env !== 'Staging') return null // FDE mode: builds and evidence only, never the workflow UI (ADR 0032)

  let body
  if (!workflowId) body = <Empty icon="file" title="Not committed yet">Staging shows the workflow after you Commit it on the Validation screen.</Empty>
  else if (error) {
    body = isForbidden(error)
      ? <ErrorState title="You can't see Staging for this project">Your account is not assigned to this project.</ErrorState>
      : isNotAvailable(error)
        ? <Empty icon="file" title="Nothing in Staging yet" action={<button className="btn btn-secondary" onClick={() => goScreen('4.9', true)}>Go to Validation</button>}>
            This workflow has not been committed, or it is not in a project assigned to you.
          </Empty>
        : <ErrorState title="Could not load Staging" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  } else if (isLoading || !data) body = <Skeleton rows={8} label="Loading Staging" />
  else {
    body = <>
      <Strip s={data} />
      <Stage s={data} projectId={projectId} onRetry={() => void refetch()} />
      <Section title="Versions"><History rows={data.history ?? []} /></Section>
      {data.state !== 'NOT_DEPLOYED' && <RequestChange workflowId={workflowId} />}
    </>
  }
  return (
    <Pane id="center-title" kicker="Staging" title="Workflow UI" className="center"
      actions={<span className="pill tone-warn"><Icon name="lock" size={11} />Staging, not production</span>}>
      <div className="stg" data-state={data?.state}>{body}</div>
    </Pane>
  )
}
