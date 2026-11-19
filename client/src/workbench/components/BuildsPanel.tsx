import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { BuildRow, GateResult } from '@/contracts'
import {
  canRetry, gateLabel, GATE_STATUS_LABEL, gateTone, hasFailedGate, isForbidden, MAX_REASON, newRetryKey, problemText, reachedGates,
  sendRetry, stateLabel, stateTone, summary, useBuilds, useGates,
} from '../lib/workflowBuilds'
import { HeldPatchCard } from './HeldPatchCard'
import { Banner, Dialog, Empty, ErrorState, Icon, Section, Skeleton } from './ui'
import './buildsPanel.css'

// Translate (4.3), the builds of the workflow (#715): one row per Sub-Skill with the status of every gate it reached, the
// findings of an attempt's gates, and Retry for a Sub-Skill that waits for the FDE. The server decides every state and
// refuses a Retry that is not allowed (409); nothing here changes a gate or a build by itself.

function Forbidden() {
  return <ErrorState title="You can't see this workflow's builds">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}

function GateRows({ workflowId, row, attempt }: { workflowId: string; row: BuildRow; attempt: number }) {
  const { data, error, isLoading, refetch } = useGates(workflowId, row.sub_skill_id, attempt)
  if (error) return isForbidden(error) ? <Forbidden />
    : <ErrorState title="Could not load the gate results" action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{problemText(error)}</ErrorState>
  if (isLoading || !data) return <Skeleton rows={3} label="Loading the gate results" />
  const reached = data.gates.filter((g: GateResult) => g.status !== 'PENDING')
  if (reached.length === 0) return <p className="muted xs">No gate has run in attempt {attempt} yet.</p>
  return (
    <ul className="bld-gates" aria-label={`Gate results of ${row.skill_name}, attempt ${attempt}`}>
      {reached.map(g => (
        <li key={g.gate} data-gate={g.gate}>
          <span className={`pill tone-${gateTone(g.status)}`}>{gateLabel(g.gate)} · {GATE_STATUS_LABEL[g.status]}</span>
          {g.tool_version && <span className="muted xs mono">{g.tool_version}</span>}
          {(g.findings ?? []).length > 0 && (
            <ul className="bld-findings" aria-label={`Findings of ${gateLabel(g.gate)}`}>
              {(g.findings ?? []).map((f, i) => (
                <li key={i}>
                  <Icon name="alert" size={12} />
                  <span><code>{f.code}</code> {f.message}</span>
                  {f.file && <span className="mono xs muted">{f.file}{f.line ? `:${f.line}` : ''}</span>}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  )
}

function Row({ workflowId, row, onRetry }: { workflowId: string; row: BuildRow; onRetry: (r: BuildRow) => void }) {
  const [open, setOpen] = useState(false)
  const [attempt, setAttempt] = useState<number>(row.attempt ?? 0)
  const gates = reachedGates(row)
  const attempts = Array.from({ length: (row.attempt ?? 0) + 1 }, (_, i) => i)
  return (
    <li className={`bld-row ${hasFailedGate(row) ? 'has-fail' : ''}`} data-sub-skill={row.sub_skill_id}>
      <div className="bld-head">
        <button type="button" className="bld-toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}>
          <Icon name={open ? 'down' : 'chevron'} size={12} />
          <strong>{row.skill_name}</strong>
          <span className="mono xs muted">{row.skill_id}</span>
        </button>
        <span className={`pill tone-${stateTone(row.state)}`}>{stateLabel(row.state)}</span>
        <span className="muted xs">Stage: {row.stage.replace(/_/g, ' ')} · attempt {row.attempt ?? 0}</span>
        {row.loop_tests && <span className={`pill tone-${row.loop_tests.state === 'PASS' ? 'ok' : row.loop_tests.state === 'FAIL' ? 'err' : 'muted'}`} title="Loop tests passed of total">Tests {row.loop_tests.passed}/{row.loop_tests.total}</span>}
        {canRetry(row.state) && <button type="button" className="btn btn-secondary sm" onClick={() => onRetry(row)} aria-label={`Retry ${row.skill_name}`}><Icon name="refresh" size={12} />Retry</button>}
      </div>
      {gates.length > 0
        ? <ul className="bld-chips" aria-label={`Gates of ${row.skill_name}`}>{gates.map(g => <li key={g.name}><span className={`pill tone-${gateTone(g.status)}`} title={`${g.label}: ${GATE_STATUS_LABEL[g.status]}`}>{g.label}</span></li>)}</ul>
        : <p className="muted xs">No gate has run yet.</p>}
      {row.state === 'NEEDS_FDE_REVIEW' && <HeldPatchCard workflowId={workflowId} subId={row.sub_skill_id} name={row.skill_name} />}
      {open && (
        <div className="bld-detail">
          {attempts.length > 1 && (
            <div className="bld-attempts" role="group" aria-label="Attempt">
              {attempts.map(n => <button key={n} type="button" aria-pressed={attempt === n} onClick={() => setAttempt(n)}>Attempt {n}</button>)}
            </div>
          )}
          <GateRows workflowId={workflowId} row={row} attempt={attempt} />
        </div>
      )}
    </li>
  )
}

function RetryDialog({ workflowId, row, onClose }: { workflowId: string; row: BuildRow | undefined; onClose: (sent?: string) => void }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const key = useRef(newRetryKey())
  const ok = reason.trim().length > 0 && reason.length <= MAX_REASON
  const close = (sent?: string) => { setReason(''); setError(undefined); setBusy(false); key.current = newRetryKey(); onClose(sent) }
  const send = async () => {
    if (!row || !ok) return
    setBusy(true)
    setError(undefined)
    try {
      const r = await sendRetry(workflowId, row.sub_skill_id, reason.trim(), key.current)
      close(`Retry accepted for ${row.skill_name}: build ${r.build_no} started.`)
    } catch (e) {
      setError(problemText(e))
      setBusy(false)
    }
  }
  return (
    <Dialog open={!!row} title={`Retry ${row?.skill_name ?? ''}`} onClose={() => close()} footer={<>
      <button className="btn btn-secondary" onClick={() => close()}>Cancel</button>
      <button className="btn btn-primary" aria-disabled={!ok || busy || undefined} onClick={() => void send()}>{busy ? 'Sending...' : 'Retry'}</button></>}>
      <p className="muted xs">The build starts again from the approved ISATVON. Say why, in plain words; it is recorded.</p>
      <label className="xs muted" htmlFor="bld-reason">Reason</label>
      <textarea id="bld-reason" className="bld-reason" rows={3} maxLength={MAX_REASON} value={reason} onChange={e => setReason(e.target.value)} disabled={busy} />
      <span className="muted xs">{reason.length}/{MAX_REASON}</span>
      {error && <Banner tone="err" icon="alert">{error}</Banner>}
    </Dialog>
  )
}

/** The panel body: loading, error, forbidden, empty and the rows. */
export function BuildsPanel({ workflowId }: { workflowId?: string }) {
  const { data, error, isLoading, refetch } = useBuilds(workflowId)
  const qc = useQueryClient()
  const [retrying, setRetrying] = useState<BuildRow>()
  const [sent, setSent] = useState<string>()
  if (!workflowId) return <Empty icon="file" title="No builds yet">The builds appear here once the build has started.</Empty>
  if (error) return isForbidden(error) ? <Forbidden />
    : <ErrorState title="Could not load the builds" action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{problemText(error)}</ErrorState>
  if (isLoading || !data) return <Skeleton rows={5} label="Loading the builds" />
  const rows = data.sub_skills
  if (rows.length === 0) return <Empty icon="file" title="Nothing is building">This workflow has no Sub-Skills to build yet.</Empty>
  const s = summary(rows)
  const wg = Object.entries(data.workflow_gates ?? {}).filter(([, v]) => v && v !== 'PENDING')
  return (
    <div className="bld" aria-label="Builds" data-testid="builds">
      <div className="bld-summary" role="group" aria-label="Build summary">
        <strong>{s.total} Sub-Skill{s.total === 1 ? '' : 's'}</strong>
        <span className="pill tone-muted">{stateLabel(data.workflow_state)}</span>
        {s.failed > 0 && <span className="pill tone-err">{s.failed} with a failed gate</span>}
        {s.review > 0 && <span className="pill tone-warn">{s.review} waiting for you</span>}
        {data.active && <span className="muted xs"><Icon name="spinner" size={11} spin /> Updating while the build runs</span>}
      </div>
      {sent && <Banner tone="ok" icon="check">{sent}</Banner>}
      {wg.length > 0 && (
        <Section title="Workflow gates">
          <ul className="bld-chips" aria-label="Workflow gates">
            {wg.map(([name, status]) => <li key={name}><span className={`pill tone-${gateTone(status!)}`}>{gateLabel(name)} · {GATE_STATUS_LABEL[status!]}</span></li>)}
          </ul>
        </Section>
      )}
      <ul className="bld-rows" aria-label="Sub-Skill builds">
        {rows.map(r => <Row key={r.sub_skill_id} workflowId={workflowId} row={r} onRetry={setRetrying} />)}
      </ul>
      <RetryDialog workflowId={workflowId} row={retrying} onClose={m => { setRetrying(undefined); if (m) { setSent(m); void qc.invalidateQueries() } }} />
      <p className="muted xs">Read only, except Retry on a Sub-Skill that waits for you. The server decides every state.</p>
    </div>
  )
}
