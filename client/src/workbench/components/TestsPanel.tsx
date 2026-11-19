import { useState } from 'react'
import type { HarnessTrace, TestCaseOut, TestRunOut } from '@/contracts'
import { projectIdOfPath } from '../lib/evidence'
import {
  isForbidden, jsonText, kindLabel, passText, problemText, resultsByCase, RUN_LABEL, runTone, sandboxLogsUrl, useSubSkillTests,
  useTestRuns, useTestRunTraces,
} from '../lib/subSkillTests'
import { useBuilds } from '../lib/workflowBuilds'
import { Banner, Copy, Empty, ErrorState, Icon, Section, Skeleton, StatusPill, Time } from './ui'
import './testsPanel.css'

// Translate (4.3), the tests of one Sub-Skill, read only (#715): the synthetic test data, the sandbox runs with each
// assertion's result, the harness trace of every case, and the run's sandbox log as a download. Nothing here edits a case
// or starts a run; a run on a build that is not certifying says so.

function Forbidden() {
  return <ErrorState title="You can't see these tests">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}
const retryAction = (refetch: () => unknown) => <button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>
const Json = ({ label, value }: { label: string; value: unknown }) => (
  <details className="tp-json"><summary>{label}</summary><pre tabIndex={0} aria-label={label}>{jsonText(value)}</pre></details>
)

function Cases({ cases }: { cases: TestCaseOut[] }) {
  return (
    <ul className="tp-cases" aria-label="Test cases">
      {cases.map(c => (
        <li key={c.name}>
          <div className="tp-case-head"><strong>{c.name}</strong><span className="pill tone-muted">{kindLabel(c.kind)}</span><span className="muted xs">ends {c.expected_terminal.toLowerCase().replace('_', ' ')}</span></div>
          <Json label={`Input of ${c.name}`} value={c.input} />
          {c.expected_output && <Json label={`Expected output of ${c.name}`} value={c.expected_output} />}
        </li>
      ))}
    </ul>
  )
}

function TestData({ workflowId, subId }: { workflowId: string; subId: string }) {
  const { data, error, isLoading, refetch } = useSubSkillTests(workflowId, subId)
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load the test data" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !data) return <Skeleton rows={4} label="Loading the test data" />
  const d = data.dataset
  if (!d) return <Empty icon="file" title="No test data yet">The build generates synthetic test cases for each Sub-Skill before it runs them.</Empty>
  return (
    <>
      <dl className="kv inline">
        <div><dt>Version</dt><dd className="mono">{d.version}</dd></div>
        <div><dt>Cases</dt><dd>{d.cases.length}</dd></div>
        <div><dt>Data</dt><dd>{d.edited_by_fde ? 'Edited by you' : 'Synthetic, generated'}</dd></div>
        <div><dt>Hash</dt><dd><Copy value={d.sha256} display={d.sha256.slice(0, 12)} label="test data hash" /></dd></div>
        <div><dt>Created</dt><dd><Time iso={d.created_at} /></dd></div>
      </dl>
      <Cases cases={d.cases} />
    </>
  )
}

function Trace({ t }: { t: HarnessTrace }) {
  return (
    <section className="tp-trace" aria-label={`Trace of ${t.case_id}`}>
      <header><strong>{t.case_id}</strong><span className="muted xs">{t.terminal ?? 'no end state'} · {t.iterations} iteration{t.iterations === 1 ? '' : 's'} · {t.handoff_rows} handoff row{t.handoff_rows === 1 ? '' : 's'}</span></header>
      {t.error && <Banner tone="err" icon="alert"><strong>{t.error.category.replace(/_/g, ' ').toLowerCase()}</strong> in the {t.error.step} step of pass {t.error.pass}: {t.error.message}</Banner>}
      <ol className="tp-passes" aria-label={`Passes of ${t.case_id}`}>
        {t.passes.map(p => (
          <li key={p.pass}>
            <span className="mono xs">pass {p.pass}</span>
            <span className={`pill tone-${p.gate_eval.success ? 'ok' : p.gate_eval.escalation ? 'err' : 'warn'}`}>{p.gate_eval.success ? 'Gate passed' : p.gate_eval.escalation ? 'Escalated' : 'Gate not met'}</span>
            <span className="muted xs">action {p.action_ms} ms · check {p.check_ms} ms</span>
            {p.ui_update && <span className="tp-ui xs">{p.ui_update}</span>}
          </li>
        ))}
      </ol>
    </section>
  )
}

function Traces({ workflowId, subId, runId }: { workflowId: string; subId: string; runId: string }) {
  const { traces, error, isLoading, refetch } = useTestRunTraces(workflowId, subId, runId)
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load the traces" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !traces) return <Skeleton rows={3} label="Loading the traces" />
  if (traces.length === 0) return <p className="muted xs">No trace was recorded for this run.</p>
  return <div className="tp-traces">{traces.map(t => <Trace key={t.case_id} t={t} />)}</div>
}

function Run({ run, workflowId, subId, projectId }: { run: TestRunOut; workflowId: string; subId: string; projectId?: string }) {
  const [traces, setTraces] = useState(false)
  const cases = resultsByCase(run)
  return (
    <li className="tp-run" data-run={run.id}>
      <div className="tp-run-head">
        <span className={`pill tone-${runTone(run.state)}`}>{RUN_LABEL[run.state]}</span>
        <strong>{passText(run)}</strong>
        <span className="muted xs"><Time iso={run.created_at} /></span>
        <Copy value={run.suite_sha256} display={run.suite_sha256.slice(0, 10)} label="suite hash" />
        {projectId && <a className="btn btn-secondary sm" href={sandboxLogsUrl(projectId, workflowId, run.sandbox_run_id)} download aria-label={`Download the sandbox log of the run from ${run.created_at}`}><Icon name="download" size={12} />Sandbox log</a>}
      </div>
      {run.non_certifying && <Banner tone="info" icon="alert">This run does not certify: it ran on a sandbox whose result never counts toward certification.</Banner>}
      {cases.length > 0 && (
        <ul className="tp-results" aria-label="Results by case">
          {cases.map(c => (
            <li key={c.name}>
              <StatusPill status={c.passed ? 'pass' : 'fail'} /><strong className="xs">{c.name}</strong>
              <span className="muted xs">{c.results.map(r => `${r.type.replace(/_/g, ' ')} ${r.passed ? 'passed' : 'failed'}${r.message ? ` (${r.message})` : ''}`).join(' · ')}</span>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn btn-secondary sm" aria-expanded={traces} onClick={() => setTraces(t => !t)}><Icon name={traces ? 'down' : 'chevron'} size={12} />{traces ? 'Hide the traces' : 'Show the traces'}</button>
      {traces && <Traces workflowId={workflowId} subId={subId} runId={run.id} />}
    </li>
  )
}

function Runs({ workflowId, subId }: { workflowId: string; subId: string }) {
  const { runs, error, isLoading, refetch } = useTestRuns(workflowId, subId)
  const projectId = typeof window === 'undefined' ? undefined : projectIdOfPath(window.location.pathname)
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load the test runs" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !runs) return <Skeleton rows={4} label="Loading the test runs" />
  if (runs.length === 0) return <Empty icon="file" title="Tests not run yet">The build runs the test cases in the sandbox once the Sub-Skill&apos;s code passes its gates.</Empty>
  return <ul className="tp-runs" aria-label="Test runs">{runs.map(r => <Run key={r.id} run={r} workflowId={workflowId} subId={subId} projectId={projectId} />)}</ul>
}

/** The panel body: pick a Sub-Skill, then its test data and runs. Loading, empty, error and forbidden are shown. */
export function TestsPanel({ workflowId }: { workflowId?: string }) {
  const builds = useBuilds(workflowId)
  const [sub, setSub] = useState<string>()
  if (!workflowId) return <Empty icon="file" title="No tests yet">The tests appear here once the build has started.</Empty>
  if (builds.error) return isForbidden(builds.error) ? <Forbidden /> : <ErrorState title="Could not load the Sub-Skills" action={retryAction(builds.refetch)}>{problemText(builds.error)}</ErrorState>
  if (builds.isLoading || !builds.data) return <Skeleton rows={5} label="Loading the Sub-Skills" />
  const rows = builds.data.sub_skills
  if (rows.length === 0) return <Empty icon="file" title="Nothing is building">This workflow has no Sub-Skills to test yet.</Empty>
  const id = rows.some(r => r.sub_skill_id === sub) ? sub! : rows[0]!.sub_skill_id
  return (
    <div className="tp" aria-label="Tests" data-testid="tests">
      <div className="tp-pick">
        <label className="xs muted" htmlFor="tp-sub">Sub-Skill</label>
        <select id="tp-sub" value={id} onChange={e => setSub(e.target.value)}>{rows.map(r => <option key={r.sub_skill_id} value={r.sub_skill_id}>{r.skill_name} ({r.skill_id})</option>)}</select>
      </div>
      <Section title="Test runs"><Runs key={`r-${id}`} workflowId={workflowId} subId={id} /></Section>
      <Section title="Test data"><TestData key={`d-${id}`} workflowId={workflowId} subId={id} /></Section>
      <p className="muted xs">Read only. Test data comes from the build and a run never certifies by itself.</p>
    </div>
  )
}
