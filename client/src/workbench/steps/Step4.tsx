import { Fragment, useEffect, useRef, useState } from 'react'
import { api } from '../api/adapter'
import type { Assertion, Snapshot, Tests } from '../api/types'
import type { StepProps } from '../App'
import { useAction, useSkills } from '../lib/data'
import { setUi } from '../lib/store'
import { goScreen } from '../lib/route'
import { currentTests } from '../lib/certification'
import { CertChecklist, DemoTag } from '../components/release'
import { Banner, Confirm, Copy, Dialog, Empty, GateButton, Icon, Kpis, Pane, Section, Skeleton, StageList, StatusPill, Time, fmtTime, shortHash } from '../components/ui'
import { tips } from '../lib/actionTips'
import { approve, useApprovals } from '../lib/approvals'

const running = (s?: { status: string }) => s?.status === 'running' || s?.status === 'queued'

// Validation (15 Oct 2026, BuLLM confirmed workflow): Sandbox · Check · Certify · Commit over the SAME backend step 4
// and gates. Fully human-gated (18 Oct 2026, per user): every step ends with the FDE's Start / Approve / Retry and
// nothing advances by itself. The backend runs the sandbox and then the checks as ONE run (runTests), so Sandbox shows
// the environment stage and Check the assertion stage of that run; Sandbox and Check approvals are client-side
// (lib/approvals.ts) until the backend records them. Sandbox runs the isolated environment (runTests), Check shows the results and evidence, Certify records the
// FDE's review of the checked artifact for this exact hash (markTestsReviewed), Commit freezes it in BAM_db and then
// deploys it to Staging (the existing sequence). One primary action per step, in the pinned footer.
type Part = 'sandbox' | 'check' | 'certify' | 'commit' | 'staging'
const PART: Record<Part, { kicker: string; title: string }> = {
  sandbox: { kicker: 'Create the isolated validation environment', title: 'Sandbox' },
  check: { kicker: 'Validation evidence', title: 'Check' },
  certify: { kicker: 'Approve before Commit', title: 'Certify' },
  commit: { kicker: 'Save to BAM_db', title: 'Commit' },
  staging: { kicker: 'Commit · Staging status', title: 'Staging complete' },
}
export const SandboxCenter = (p: StepProps) => <Center {...p} part="sandbox" />
export const TestCenter = (p: StepProps) => <Center {...p} part="check" />
export const CertificationCenter = (p: StepProps) => <Center {...p} part="certify" />
export const HandoverCenter = (p: StepProps) => <Center {...p} part="commit" />
export const StagingCenter = (p: StepProps) => <Center {...p} part="staging" />

function useValidation(snap: Snapshot) {
  const m = snap.workflow?.manifest
  // Only results for THIS manifest hash count; an older run is shown as stale history.
  const t = currentTests(snap)
  const p = snap.persist
  const testing = running(t?.sandbox) || running(t?.promptfoo)
  const testsPassed = t?.promptfoo.status === 'succeeded'
  const testsFailed = t?.sandbox.status === 'failed' || t?.promptfoo.status === 'failed'
  return { m, t, p, testing, testsPassed, testsFailed, reviewed: !!t?.reviewedAt && testsPassed, committing: p.artifacts.some(running),
    isCommitted: !!m && p.committed?.hash === m.hash, deploying: running(p.deploy), isDeployed: !!m && p.deployed?.hash === m.hash }
}

function Center({ snap, part }: StepProps & { part: Part }) {
  const { rows } = useSkills(snap.skillsRevision)
  const v = useValidation(snap)
  const { m, t } = v
  const staleRun = !t ? snap.tests : undefined
  const shown = t ?? staleRun
  const name = (id: string) => rows?.find(r => r.id === id)?.name ?? id
  const passed = shown?.assertions.filter(a => a.status === 'pass').length ?? 0
  const cert = m ? [...snap.versions].reverse().find(x => x.kind === 'certified' && x.hash === m.hash) : undefined
  const P = PART[part]
  const row = (icon: string, label: string, detail: string, pill: string, tone?: string) =>
    <li key={label}><Icon name={icon} size={15} /><div><strong>{label}</strong><span className="muted xs">{detail}</span></div><span className={`dc-pill ${tone ?? ''}`}>{pill}</span></li>
  const sb = t?.sandbox
  return (
    <Pane id="center-title" kicker={P.kicker} title={P.title} className="center"
      actions={m && <span className="mono xs muted">manifest {m.version} · {shortHash(m.hash)}</span>}>
      {part === 'sandbox' && <>
        <ul className="dc-list">
          {row('tb-box', 'Sandbox environment', sb ? `Isolated sandbox · ${sb.status}${sb.error ? `: ${sb.error}` : ''}` : 'Created for each run, isolated from BAM_db and Staging',
            !sb ? 'Not run' : running(sb) ? 'Running' : sb.status === 'succeeded' ? 'Complete' : sb.status === 'failed' ? 'Failed' : sb.status, sb?.status === 'succeeded' ? 'tone-ok' : sb?.status === 'failed' ? 'tone-err' : running(sb) ? 'tone-info' : undefined)}
          {row('tb-code', 'Artifact version', m ? `Current Translate output · ${m.version} · ${shortHash(m.hash)}` : 'No translated artifact yet',
            m && snap.workflow?.approval?.hash === m.hash ? 'Attached' : 'Not approved', m && snap.workflow?.approval?.hash === m.hash ? 'tone-ok' : undefined)}
          {row('tb-list-check', 'Test inputs', t ? `${t.assertions.length} validation fixtures and assertions` : 'Validation fixtures and assertions run with the sandbox',
            !t ? 'With the run' : t.assertions.length ? 'Attached' : '0 fixtures - add one to enable testing', !t ? undefined : t.assertions.length ? 'tone-ok' : 'tone-warn')}
        </ul>
        {staleRun && <Banner tone="warn" icon="refresh"><strong>Earlier run is for another artifact.</strong> It tested {staleRun.manifestVersion} ({shortHash(staleRun.manifestHash)}); run the sandbox for {m?.version}.</Banner>}
        {t && <StageList stages={[t.sandbox]} />}
        <Section title="Run boundary"><p className="muted sm">Creates an isolated environment and deploys the generated skills into it. The checks on them follow on Check. Nothing is committed to BAM_db.</p></Section>
      </>}
      {part === 'check' && <>
        <Kpis label="Check summary" items={[
          { value: shown ? shown.assertions.length : '—', label: 'Checks' },
          { value: shown ? passed : '—', label: 'Passed', tone: shown && passed === shown.assertions.length && shown.assertions.length ? 'ok' : undefined },
          { value: shown ? shown.assertions.length - passed : '—', label: 'Failed', tone: shown && shown.assertions.length - passed ? 'err' : undefined },
        ]} />
        {!shown ? <Empty icon="tb-list-check" title="No results for this artifact">{m ? `Run the sandbox for ${m.version} (${shortHash(m.hash)}) first.` : 'Results appear after the sandbox run.'}</Empty> : <>
          {staleRun && <Banner tone="warn" icon="refresh"><strong>Stale results.</strong> This run tested manifest {staleRun.manifestVersion} ({shortHash(staleRun.manifestHash)}); the current manifest is {m?.version} ({m ? shortHash(m.hash) : '—'}). They are kept as history and cannot certify the edited artifact. Run the sandbox again.</Banner>}
          <dl className="kv inline">
            <div><dt>Run</dt><dd><Copy value={shown.runId} label="test run ID" /></dd></div>
            <div><dt>Tested manifest</dt><dd><span className="mono">{shown.manifestVersion}</span> · <Copy value={shown.manifestHash} display={shortHash(shown.manifestHash)} label="tested hash" /></dd></div>
            <div><dt>Approved SOW</dt><dd className="mono">v{shown.sowVersion}</dd></div>
            <div><dt>Started</dt><dd><Time iso={shown.startedAt} /></dd></div>
          </dl>
          <StageList stages={[shown.promptfoo]} />
          <Assertions t={shown} name={name} testing={v.testing} />
        </>}
        <div className="dc-rows">
          {snap.testHistory.length > 0 && <details>
            <summary><strong>Earlier runs</strong><span className="dc-count">{snap.testHistory.length}</span><Icon name="down" size={13} /></summary>
            <ul className="run-history">{snap.testHistory.map(h => (
              <li key={h.runId}><span className="mono xs">{h.runId}</span><span className="mono xs">{h.manifestVersion} · {h.manifestHash.slice(0, 8)}</span>
                <StatusPill status={h.promptfoo.status === 'succeeded' ? 'pass' : h.promptfoo.status === 'failed' ? 'fail' : h.promptfoo.status} text={h.promptfoo.status === 'succeeded' ? `Passed${h.reviewedAt ? ' · certified' : ''}` : undefined} />
                <Time iso={h.startedAt} /></li>
            ))}</ul>
          </details>}
        </div>
      </>}
      {part === 'certify' && <>
        <Section title="Certification checklist" aside={<DemoTag />}><CertChecklist snap={snap} /></Section>
        <Section title="Certification record">
          {cert ? <dl className="kv">
            <div><dt>Certified version</dt><dd className="mono">{cert.version}</dd></div>
            <div><dt>Manifest hash</dt><dd><Copy value={cert.hash} display={shortHash(cert.hash)} label="certified hash" /></dd></div>
            {t && <div><dt>Checked by run</dt><dd><Copy value={t.runId} label="test run ID" /> · {passed} of {t.assertions.length} assertions passed</dd></div>}
            {t && <div><dt>SOW</dt><dd className="mono">v{t.sowVersion} (approved by the FDE)</dd></div>}
            <div><dt>Certified at</dt><dd><Time iso={cert.at} /> <DemoTag /></dd></div>
          </dl> : <Empty icon="tb-certificate" title="Not certified yet">{v.testsPassed ? 'The artifact and its evidence are ready. Certification is recorded against this exact artifact version.' : 'Certification needs a clean Check for this exact manifest hash.'}</Empty>}
        </Section>
      </>}
      {part === 'commit' && <>
        <HandoverPackage snap={snap} />
        <Section title="Commit summary"><p className="muted sm">Destination: BAM_db. The certified JSON contract and translated code are saved together as the frozen version. Approve saves it, then goes live on Staging; the FDE / Staging selector never deploys.</p></Section>
        <details className="accordion"><summary><Icon name="rocket" size={12} />Staging status<span className="muted xs">{v.isDeployed ? 'Live' : snap.persist.deployed ? 'Older version live' : 'Nothing deployed'}</span></summary><StagingSummary snap={snap} /></details>
      </>}
      {part === 'staging' && <StagingSummary snap={snap} />}
    </Pane>
  )
}

// ---- pinned footers: one primary action each ------------------------------------------------------------------
const FootIn = ({ title, line, children }: { title: string; line?: string; children: React.ReactNode }) => (
  <div className="ws-foot-in" role="status" aria-live="polite">
    <div className="ws-foot-text"><strong>{title}</strong>{line && <span className="muted xs">{line}</span>}</div>{children}
  </div>
)
export function SandboxFooter({ snap, info }: StepProps) {
  const v = useValidation(snap)
  const a = useApprovals()
  const { busy, run } = useAction()
  const ro = info.editable ? undefined : 'This screen is read-only'
  const sb = v.t?.sandbox
  const start = () => void run('Run sandbox', () => api.runTests(), { step: 4 })
  if (sb && running(sb) || busy === 'Run sandbox') return <FootIn title="Setting up the sandbox..." line="Deploying the generated skills into an isolated environment">{null}</FootIn>
  if (sb?.status === 'failed') return <FootIn title="The sandbox could not be set up" line={sb.error ?? 'No reason reported'}>
    <GateButton icon="refresh" {...tips('4.8', 'Retry')} reason={ro} onClick={start}>Retry</GateButton></FootIn>
  // Approve accepts the sandbox for this hash and opens Check. It stays after approval (same handler; approving the
  // same hash again is a no-op) so the approved footer still leads on to Check (4-word rule: no "Continue" button).
  const approveBtn = <GateButton icon="tb-box" {...tips('4.8', 'Approve')} reason={ro} onClick={() => { approve({ sandbox: v.m!.hash }); goScreen('4.4') }}>Approve</GateButton>
  if (sb?.status === 'succeeded') return a.sandbox === v.m?.hash
    ? <FootIn title="Approved" line="The sandbox is ready; the checks run on Check">{approveBtn}</FootIn>
    : <FootIn title="Sandbox ready" line={`Isolated environment for ${v.m?.version} · ${v.m ? shortHash(v.m.hash) : ''}`}>{approveBtn}</FootIn>
  return <FootIn title="Ready" line="Uses the current Translate output and its test inputs">
    <GateButton icon="tb-box" {...tips('4.8', 'Start')} reason={ro ?? (!v.m ? 'No translated artifact yet' : snap.workflow?.approval?.hash !== v.m.hash ? 'Approve the generated skills in Translate first' : undefined)} onClick={start}>Start</GateButton></FootIn>
}
/** Check: the checks of the approved sandbox's run. Approve (aria-disabled until every check passes) opens Certify;
 *  Retry runs the sandbox and the checks again (one backend run). No Start of its own: see the note at the top. */
export function CheckFooter({ snap, info }: StepProps) {
  const v = useValidation(snap)
  const a = useApprovals()
  const { busy, run } = useAction()
  const ro = info.editable ? undefined : 'This screen is read-only'
  const t = v.t, pf = t?.promptfoo
  const sandboxOk = !!t && a.sandbox === v.m?.hash && t.sandbox.status === 'succeeded'
  if (!sandboxOk) return <FootIn title="Waiting for the sandbox" line="Start and approve the sandbox first">{null}</FootIn>
  if (running(pf) || busy === 'Run sandbox') return <FootIn title="Running checks..." line={`${t!.assertions.length} results so far`}>{null}</FootIn>
  const passed = t!.assertions.filter(x => x.status === 'pass').length
  if (pf?.status === 'failed') return <FootIn title="Some checks failed" line={`${passed} of ${t!.assertions.length} checks passed`}>
    <GateButton icon="refresh" {...tips('4.4', 'Retry')} reason={ro} onClick={() => void run('Run sandbox', () => api.runTests(), { step: 4 })}>Retry</GateButton></FootIn>
  if (a.check === t!.runId) return <FootIn title="Approved" line={`${passed} of ${t!.assertions.length} checks passed · Certify is next`}>{null}</FootIn>
  return <FootIn title="Checks finished" line={`${passed} of ${t!.assertions.length} checks passed`}>
    <GateButton icon="tb-list-check" {...tips('4.4', 'Approve')} reason={ro ?? (pf?.status === 'succeeded' ? undefined : 'Available once all checks pass.')}
      onClick={() => { approve({ check: t!.runId }); goScreen('4.5') }}>Approve</GateButton></FootIn>
}
export function CertifyFooter({ snap, info }: StepProps) {
  const v = useValidation(snap)
  const a = useApprovals()
  const { busy, run } = useAction()
  const ro = info.editable ? undefined : 'This screen is read-only'
  if (v.reviewed) return <FootIn title="Certified" line={`Recorded ${fmtTime(v.t!.reviewedAt!)} for ${v.m?.version}`}>{null}</FootIn>
  const reason = ro ?? (!v.t ? 'Run the sandbox for this artifact first' : v.testing ? 'The run is still in progress' : !v.testsPassed ? 'All checks must pass first'
    : a.check !== v.t.runId ? 'Approve the check results in Check first' : undefined)
  const t = tips('4.5', 'Approve')
  return <FootIn title="Approval applies to this artifact version" line={v.m ? `${v.m.version} · ${shortHash(v.m.hash)}` : undefined}>
    <GateButton action="review" {...t} tipReason={ro ?? t.tipReason} reason={reason} busy={busy === 'Certify'}
      onClick={() => void run('Certify', () => api.markTestsReviewed().then(() => true), { step: 4 }).then(ok => { if (ok) goScreen('4.6') })}>Approve</GateButton></FootIn>
}
/** Commit: Approve is the final go-live. It asks first ("Go live?"), then saves the certified version to BAM_db (the
 *  frozen version) and deploys it to Staging as soon as the save is confirmed. A failed save or deploy shows Retry. */
export function CommitFooter({ snap, info }: StepProps) {
  const v = useValidation(snap)
  const { busy, run } = useAction()
  const [ask, setAsk] = useState(false)
  const [failed, setFailed] = useState<'commit' | 'deploy' | null>(null)
  const { m, p } = v
  const ro = info.editable ? undefined : 'This screen is read-only'
  const goLive = useRef(false)
  const deploy = () => {
    goLive.current = false; setFailed(null)
    void run('Deploy', () => api.deployToStaging().then(() => true), { step: 4 }).then(ok => { if (!ok) setFailed('deploy') })
  }
  const commit = () => {
    goLive.current = true; setFailed(null)
    void run('Commit', () => api.commitToPostgres().then(() => true), { step: 4 }).then(ok => { if (!ok) { goLive.current = false; setFailed('commit') } })
  }
  // The save is confirmed: go live now (the user already confirmed). When the deploy lands, show the Staging view.
  useEffect(() => { if (goLive.current && v.isCommitted) deploy() }, [v.isCommitted]) // eslint-disable-line react-hooks/exhaustive-deps
  const prevDeployed = useRef(v.isDeployed)
  useEffect(() => { if (v.isDeployed && !prevDeployed.current) setUi({ env: 'Staging' }); prevDeployed.current = v.isDeployed }, [v.isDeployed, m])
  const confirm = () => { setAsk(false); if (v.isCommitted) deploy(); else commit() }
  const dialog = <Dialog open={ask} title="Go live?" onClose={() => setAsk(false)} footer={<>
      <button className="btn btn-secondary" onClick={() => setAsk(false)}>Cancel</button>
      <button className="btn btn-primary" onClick={confirm} autoFocus>Approve</button></>}>
    <p>This saves the certified skills and makes them available on Staging.</p>
  </Dialog>
  if (v.isDeployed) return <FootIn title={`Live on Staging · ${m?.version}`} line="Staging is not production">{null}</FootIn>
  const t = tips('4.6', 'Approve')
  const working = v.committing || v.deploying || busy === 'Commit' || busy === 'Deploy'
  const reason = ro ?? (!v.reviewed && !v.isCommitted ? 'Certify the checked artifact first' : undefined)
  return <FootIn title={failed ? `The ${failed === 'commit' ? 'save' : 'deploy'} did not finish` : v.isCommitted ? 'Certified artifact · saved to BAM_db' : 'Certified artifact · save pending'}
    line={v.isCommitted ? `Committed ${fmtTime(p.committed!.at)}` : 'Destination: BAM_db, then Staging'}>
    {failed && !working
      ? <GateButton icon="refresh" {...tips('4.6', 'Retry')} reason={ro} onClick={failed === 'deploy' || v.isCommitted ? deploy : commit}>Retry</GateButton>
      : <GateButton action="commit" {...t} tipReason={ro ?? t.tipReason} reason={reason} busy={working} successKey={p.committed?.at} onClick={() => setAsk(true)}>Approve</GateButton>}
    {dialog}</FootIn>
}


// Assertions grouped by skill. The skill label is sticky while its rows scroll; every row expands into its evidence.
function Assertions({ t, name, testing }: { t: Tests; name: (id: string) => string; testing: boolean }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(t.assertions.filter(a => a.status === 'fail').map(a => a.id)))
  const toggle = (id: string) => setOpen(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const passed = t.assertions.filter(a => a.status === 'pass').length
  const bySkill = [...new Set(t.assertions.map(a => a.skillId))]
  return (
    <Section title="Assertions" aside={t.assertions.length > 0 && <span className="muted sm">{passed} of {t.assertions.length} passed · select a row for its evidence</span>}>
      {t.assertions.length === 0 ? (testing ? <Skeleton rows={4} label="Running assertions" /> : <p className="muted sm">No assertion results reported.</p>) : (
        <div className="scroll-box assertions-box" tabIndex={0} aria-label="Assertion results">
          <table className="table assertions">
            <thead><tr><th scope="col">Assertion</th><th scope="col">Result</th></tr></thead>
            {bySkill.map(id => (
              <tbody key={id}>
                <tr className="skill-head"><th colSpan={2} scope="colgroup">{name(id)} <span className="mono xs muted">{id}</span></th></tr>
                {t.assertions.filter(a => a.skillId === id).map(a => (
                  <Fragment key={a.id}>
                    <tr className={`as-row ${a.status === 'fail' ? 'row-fail' : ''}`}>
                      <td>
                        <button className="as-toggle" aria-expanded={open.has(a.id)} aria-controls={`ev-${a.id}`} onClick={() => toggle(a.id)}>
                          <Icon name={open.has(a.id) ? 'down' : 'chevron'} size={12} />{a.name}
                        </button>
                        {a.message && <div className="stage-error xs">{a.message}</div>}
                      </td>
                      <td><StatusPill status={a.status} /></td>
                    </tr>
                    {open.has(a.id) && <tr className="as-evidence" id={`ev-${a.id}`}><td colSpan={2}><AssertionEvidence a={a} /></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </Section>
  )
}

function AssertionEvidence({ a }: { a: Assertion }) {
  return (
    <dl className="evidence">
      <div><dt>Fixture</dt><dd><Copy value={a.fixture} label="fixture path" /></dd></div>
      <div><dt>Input</dt><dd className="mono xs pre">{a.input}</dd></div>
      <div><dt>Expected</dt><dd className="xs">{a.expected}</dd></div>
      <div><dt>Observed</dt><dd className={`mono xs pre ${a.status === 'fail' ? 'tone-err' : ''}`}>{a.observed}</dd></div>
      {a.message && <div><dt>Failure</dt><dd className="stage-error xs">{a.message}</dd></div>}
      <div><dt>Evidence</dt><dd><Copy value={a.evidence} display={`${a.evidence.slice(0, 34)}…`} label="evidence reference" /> <DemoTag /></dd></div>
    </dl>
  )
}



/** What is handed over: the frozen version and its evidence, all from the backend snapshot. Export is a local
 *  download of that same record (no backend call, nothing is published). */
function HandoverPackage({ snap }: { snap: Snapshot }) {
  const m = snap.workflow?.manifest, p = snap.persist, t = currentTests(snap)
  const frozen = !!m && p.committed?.hash === m.hash
  const items = [
    { label: `SOW v${snap.sow?.approval?.version ?? '—'}`, ok: !!snap.sow?.approval, detail: snap.sow?.approval ? `Approved by ${snap.sow.approval.by}` : 'Not approved' },
    { label: 'Skills and manifest', ok: !!m && snap.workflow?.approval?.hash === m.hash, detail: m ? `${m.version} · ${shortHash(m.hash)}` : 'No manifest' },
    { label: 'Test evidence', ok: !!t?.reviewedAt, detail: t ? `${t.assertions.filter(a => a.status === 'pass').length}/${t.assertions.length} assertions, run ${t.runId}` : 'No run for this manifest' },
    { label: 'Frozen in BAM_db', ok: frozen, detail: frozen ? `Committed ${fmtTime(p.committed!.at)}` : 'Pending: Approve in Commit' },
  ]
  const exportRecord = () => {
    const blob = new Blob([JSON.stringify({ sow: snap.sow?.approval, manifest: m, tests: t && { runId: t.runId, manifestHash: t.manifestHash, reviewedAt: t.reviewedAt, assertions: t.assertions.map(a => ({ id: a.id, status: a.status })) }, committed: p.committed, versions: snap.versions.map(v => ({ kind: v.kind, version: v.version, hash: v.hash, at: v.at })) }, null, 2)], { type: 'application/json' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `bam-handover-${m?.version ?? 'draft'}.json`; a.click(); URL.revokeObjectURL(a.href)
  }
  return (
    <Section title="Commit package" aside={<button className="btn btn-secondary sm" disabled={!m} onClick={exportRecord}><Icon name="download" />Export record</button>}>
      <ol className="cert-list">{items.map(i => (
        <li key={i.label} className="cert-item">
          <StatusPill status={i.ok ? 'pass' : 'pending'} text={i.ok ? 'Included' : 'Missing'} />
          <div className="cert-main"><span className="cert-label">{i.label}</span><span className="cert-detail">{i.detail}</span></div>
        </li>))}</ol>
    </Section>
  )
}

/** Staging complete: what is live on Staging, from the backend. Staging is not production. */
function StagingSummary({ snap }: { snap: Snapshot }) {
  const p = snap.persist, m = snap.workflow?.manifest
  const live = !!m && p.deployed?.hash === m.hash
  if (!p.deployed) return <Empty icon="rocket" title="Nothing on Staging yet">The frozen version is deployed from Commit. Until then, Staging has nothing from this workflow.</Empty>
  return <>
    {!live && <Banner tone="warn" icon="refresh">Staging runs {p.deployed.version}, an older manifest than the current {m?.version}. Deploy the current version from Commit.</Banner>}
    <Kpis label="Staging summary" items={[
      { value: p.deployed.version, label: 'Live on Staging', tone: live ? 'ok' : 'warn' },
      { value: p.deployedSkills.reduce((n, g) => n + g.skills.length, 0), label: 'Skills deployed' },
      { value: <Time iso={p.deployed.at} />, label: 'Deployed' },
      { value: 'Staging', label: 'Environment (not production)' },
    ]} />
    <dl className="kv">
      <div><dt>Manifest hash</dt><dd><Copy value={p.deployed.hash} display={shortHash(p.deployed.hash)} label="deployed hash" /></dd></div>
      {p.deployed.runId && <div><dt>Run ID</dt><dd><Copy value={p.deployed.runId} label="run ID" /></dd></div>}
    </dl>
    <Section title="Deployed skills">
      {p.deployedSkills.map(g => (
        <details key={g.agent} className="accordion" open>
          <summary><Icon name="palette" size={12} />{g.agent}<span className="muted xs">{g.skills.length}</span></summary>
          <ul>{g.skills.map(x => <li key={x}>{x}</li>)}</ul>
        </details>
      ))}
    </Section>
  </>
}
