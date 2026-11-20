import { useState } from 'react'
import type { SubSkillTimeline, TimelineStep } from '@/contracts'
import {
  formatCost, formatDuration, formatTokens, idleMs, isForbidden, KIND_LABEL, problemText, stateLabel, stateTone, stepTitle, useBuildTimeline,
} from '../lib/buildTimeline'
import { gateLabel } from '../lib/workflowBuilds'
import { Empty, ErrorState, Icon, Skeleton, Time } from './ui'
import './timelinePanel.css'

// Translate (4.3), the build timeline (#926): for each Sub-Skill, its newest build as ordered steps (state changes, model
// calls, code attempts, failing gates) with duration, tokens and cost, and the reason a parked build is parked, named by
// gate and rule code. Read only; the server never sends messages, model outputs or document contents.

function Forbidden() {
  return <ErrorState title="You can't see this workflow's timeline">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}

function Findings({ step }: { step: Pick<TimelineStep, 'findings'> }) {
  const findings = step.findings ?? []
  if (findings.length === 0) return null
  return (
    <ul className="tl-findings" aria-label="Findings">
      {findings.map((f, i) => (
        <li key={i}>
          <Icon name="alert" size={12} />
          <span>{gateLabel(f.gate)} <code>{f.code}</code></span>
          {f.file && <span className="mono xs muted">{f.file}{f.line ? `:${f.line}` : ''}</span>}
          {f.package && <span className="mono xs muted">{f.package}</span>}
        </li>
      ))}
    </ul>
  )
}

function Step({ step }: { step: TimelineStep }) {
  return (
    <li className={`tl-step tone-${step.status === 'FAILED' ? 'err' : step.status === 'RUNNING' ? 'warn' : 'ok'}`} data-kind={step.kind} data-seq={step.seq}>
      <div className="tl-row">
        <span className="pill tone-muted">{KIND_LABEL[step.kind]}</span>
        <strong>{stepTitle(step)}</strong>
        {step.status === 'RUNNING' && <span className="muted xs"><Icon name="spinner" size={11} spin /> Running</span>}
        {step.cycle !== null && step.cycle !== undefined && <span className="muted xs">cycle {step.cycle}, attempt {step.attempt ?? 0}</span>}
        <span className="muted xs tl-when"><Time iso={step.started_at} /> · {formatDuration(step.duration_ms)}</span>
      </div>
      {step.kind === 'MODEL' && (
        <p className="muted xs" aria-label="Model usage">{formatTokens(step.tokens_in ?? 0)} in, {formatTokens(step.tokens_out ?? 0)} out · {formatCost(step.cost_usd ?? '0.0000')}</p>
      )}
      <Findings step={step} />
    </li>
  )
}

function Failure({ sub }: { sub: SubSkillTimeline }) {
  const f = sub.failure
  if (!f) return null
  return (
    <div className="tl-failure" role="note" aria-label={`Why ${sub.skill_name} stopped`}>
      <strong><Icon name="alert" size={13} /> Stopped: {f.event.replace(/_/g, ' ')}</strong>
      <span className="muted xs">{stateLabel(f.state)} · <Time iso={f.at} /></span>
      {(f.failing_gates ?? []).length > 0 && <span className="xs">Failing gates: {(f.failing_gates ?? []).map(gateLabel).join(', ')}</span>}
      <Findings step={f} />
    </div>
  )
}

function SubSkill({ sub }: { sub: SubSkillTimeline }) {
  const [open, setOpen] = useState(!!sub.failure)
  const [now] = useState(Date.now)
  const idle = idleMs(sub, now)
  return (
    <li className="tl-sub" data-sub-skill={sub.sub_skill_id}>
      <div className="tl-head">
        <button type="button" className="tl-toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}>
          <Icon name={open ? 'down' : 'chevron'} size={12} />
          <strong>{sub.skill_name}</strong>
          <span className="mono xs muted">{sub.skill_id}</span>
        </button>
        <span className={`pill tone-${stateTone(sub.state)}`}>{stateLabel(sub.state)}</span>
        {sub.build_id && <span className="mono xs muted" title="Build id">build {sub.build_no}</span>}
      </div>
      <p className="muted xs tl-totals" aria-label="Build totals">
        {sub.llm_calls} model call{sub.llm_calls === 1 ? '' : 's'} · {formatTokens((sub.tokens_in ?? 0) + (sub.tokens_out ?? 0))} tokens · {formatCost(sub.cost_usd ?? '0.0000')} · {sub.code_attempts ?? 0} code attempt{sub.code_attempts === 1 ? '' : 's'}
        {sub.current_stage && ` · stage ${sub.current_stage.replace(/_/g, ' ')}`}
        {idle !== null && idle > 5 * 60_000 && ` · no progress for ${formatDuration(idle)}`}
      </p>
      <Failure sub={sub} />
      {open && ((sub.steps ?? []).length === 0
        ? <p className="muted xs">This Sub-Skill has no build steps yet.</p>
        : <ol className="tl-steps" aria-label={`Steps of ${sub.skill_name}`}>{(sub.steps ?? []).map(s => <Step key={s.seq} step={s} />)}</ol>)}
    </li>
  )
}

/** The panel body: loading, error, forbidden, empty and the Sub-Skills. */
export function TimelinePanel({ workflowId }: { workflowId?: string }) {
  const { data, error, isLoading, refetch } = useBuildTimeline(workflowId)
  if (!workflowId) return <Empty icon="file" title="No timeline yet">The timeline appears here once the build has started.</Empty>
  if (error) return isForbidden(error) ? <Forbidden />
    : <ErrorState title="Could not load the timeline" action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{problemText(error)}</ErrorState>
  if (isLoading || !data) return <Skeleton rows={5} label="Loading the timeline" />
  if (data.sub_skills.length === 0) return <Empty icon="file" title="Nothing has been built">This workflow has no Sub-Skills to build yet.</Empty>
  return (
    <div className="tl" aria-label="Build timeline" data-testid="timeline">
      <div className="tl-summary" role="group" aria-label="Timeline summary">
        <strong>{data.sub_skills.length} Sub-Skill{data.sub_skills.length === 1 ? '' : 's'}</strong>
        <span className="pill tone-muted">{stateLabel(data.workflow_state)}</span>
        <span className="muted xs">Spent on the builds: {formatCost(data.cost_usd)}</span>
      </div>
      <ul className="tl-subs" aria-label="Sub-Skill timelines">{data.sub_skills.map(s => <SubSkill key={s.sub_skill_id} sub={s} />)}</ul>
    </div>
  )
}
