// Stage 3 review screens. 3.3 Skills/code review: one skill's contract, generated code diff and findings,
// read-only (editing happens in 3.2 for fields and 4.3 for code). 3.4 Manifest and deploy prep: manifest
// version, release prerequisites, version history and the hand-off into the Sandbox. Neither screen can
// approve, certify, freeze or deploy anything; those gates stay in 4.2 and 4.4.
import { ISATVON, type Snapshot } from '../api/types'
import { useCode, useSkills } from '../lib/data'
import { setUi, useUi } from '../lib/store'
import { goScreen } from '../lib/route'
import { useDrafts } from '../lib/drafts'
import { currentTests } from '../lib/certification'
import { diffWords } from '../lib/sowDiff'
import { CertChecklist, DemoTag, ManifestCompare, VersionHistory } from '../components/release'
import { Banner, Copy, Empty, GateButton, Icon, Pane, Section, Skeleton, StageList, StatusPill, Time, shortHash } from '../components/ui'
import { DiffText } from './Step2'
import { FindingItem, ReviewEditor, codeRev, openCode } from './Step3'

// ---- 3.3 Skills/code review ----------------------------------------------------------------------------
export function SkillReview({ snap }: { snap: Snapshot }) {
  const { rows, isLoading } = useSkills(snap.skillsRevision)
  const { files } = useCode(codeRev(snap))
  const picked = useUi(s => s.reviewSkill)
  const draft = useDrafts(d => d.code)
  const skill = rows?.find(r => r.id === picked) ?? rows?.[0]
  const file = files?.find(f => f.skillId === skill?.id)
  const findings = snap.workflow?.activities.flatMap(a => a.findings.filter(f => f.skillId === skill?.id)) ?? []
  const tests = currentTests(snap)
  const asserts = tests?.assertions.filter(a => a.skillId === skill?.id) ?? []
  const m = snap.workflow?.manifest
  const approved = !!m && snap.workflow?.approval?.hash === m.hash
  const agents = [...new Set((rows ?? []).map(r => r.agent))]
  const open = (id: string) => snap.workflow?.activities.flatMap(a => a.findings).filter(f => f.skillId === id && !f.resolved).length ?? 0
  return (
    <main className="panes">
      <Pane id="left-title" kicker="Context and navigation" title="Skills">
        {isLoading && !rows ? <Skeleton rows={6} /> : !rows?.length ? <Empty title="No skills yet">Skills are extracted in the Skills grid after the SOW is approved.</Empty> : agents.map(a => (
          <Section key={a} title={a}>
            <ul className="nav-list">{rows.filter(r => r.agent === a).map(r => (
              <li key={r.id}><button className="nav-item" aria-current={skill?.id === r.id || undefined} onClick={() => setUi({ reviewSkill: r.id })}>
                <span>{r.name}</span>{open(r.id) ? <StatusPill status="fail" text={`${open(r.id)}`} /> : <span className="mono xs muted">{r.id}</span>}
              </button></li>))}</ul>
          </Section>))}
      </Pane>
      <Pane id="center-title" kicker="Skill review (read-only)" title={skill ? `${skill.agent} › ${skill.name}` : 'Skill review'} className="center"
        actions={skill && <StatusPill status={skill.status === 'TEST_FAILED' ? 'fail' : skill.status === 'PARSED' ? 'queued' : 'pass'} text={skill.status} />}>
        {!skill ? <Empty icon="file" title="Nothing to review yet">Approve the SOW to extract skills, then generate the contract in the Skills grid to produce code.</Empty> : <>
          <Banner tone="info" icon="eye">Review context, read-only. Change fields in <button className="link" onClick={() => goScreen('3.2')}>Skills grid</button> and code in <button className="link" onClick={() => file ? openCode(file.path) : goScreen('4.3')}>Generated code</button>.</Banner>
          <Section title="ISATVON contract" aside={<span className="mono xs muted">{skill.id} · SOW {skill.passageId}</span>}>
            <table className="table contract">
              <tbody>{ISATVON.map(c => {
                const v = skill.fields[c.key], o = skill.original[c.key]
                return (
                  <tr key={c.key} className={skill.errors[c.key] ? 'row-fail' : ''}>
                    <th scope="row"><span className="isatvon-letter">{c.letter}</span>{c.label}</th>
                    <td>{v === o ? (v || <em className="muted">empty</em>) : <><DiffText segs={diffWords(o, v)} /><div className="muted xs">edited since extraction</div></>}
                      {skill.errors[c.key] && <div className="mark-error"><Icon name="alert" size={10} />{skill.errors[c.key]}</div>}</td>
                  </tr>
                )
              })}</tbody>
            </table>
          </Section>
          <Section title="Generated code" aside={file && <Copy value={file.path} label="file path" />}>
            {!file ? <p className="muted sm">No code yet: Validation activity C renders it after the ISATVON Contract is generated in the Skills grid.</p> : <>
              {draft[file.path] !== undefined && draft[file.path] !== file.content && <Banner tone="warn" icon="edit">This file has unsaved edits in 4.3. They are not shown here until saved.</Banner>}
              <p className="muted xs">{file.edited ? 'Edited by a reviewer; compared with the generated version' : 'Unedited generated code'} <span className="diff-legend"><ins className="d-added">Added</ins><del className="d-removed">Removed</del></span></p>
              <div className="monaco-box short">
                <ReviewEditor path={`review/${file.path}`} language="python" original={file.generated} value={file.content} editable={false}
                  lockedReason="Read-only review. Edit this file in Generated code." showChanges onChange={() => {}} onSave={() => {}} />
              </div>
              <button className="link sm" onClick={() => openCode(file.path)}><Icon name="link" size={12} />Edit in Generated code</button>
            </>}
          </Section>
          <Section title="Review findings" aside={<span className="muted sm">{findings.filter(f => !f.resolved).length} open</span>}>
            {findings.length === 0 ? <p className="muted sm">{snap.workflow ? 'No findings for this skill.' : 'Findings come from the validation run.'}</p>
              : <ul className="findings compact">{findings.map(f => <FindingItem key={f.id} f={f} files={files} />)}</ul>}
          </Section>
        </>}
      </Pane>
      <Pane id="right-title" kicker="Evidence and status" title="Review evidence">
        <dl className="kv">
          <div><dt>Manifest</dt><dd>{m ? <><span className="mono">{m.version}</span> · <Copy value={m.hash} display={m.hash.slice(0, 8)} label="manifest hash" /></> : 'Not generated'}</dd></div>
          <div><dt>Code approval</dt><dd>{approved ? <StatusPill status="done" text="Approved" /> : <StatusPill status="pending" text="Not approved" />}</dd></div>
          <div><dt>Test run</dt><dd>{tests ? <span className="mono xs">{tests.runId}</span> : snap.tests ? <StatusPill status="stale" text="Stale (other hash)" /> : 'Not run'}</dd></div>
        </dl>
        <Section title="Assertions for this skill">
          {asserts.length === 0 ? <p className="muted sm">No results for the current manifest. Tests run in 4.4.</p>
            : <ul className="mini-list">{asserts.map(a => <li key={a.id}><StatusPill status={a.status} /><span className="sm">{a.name}</span></li>)}</ul>}
        </Section>
      </Pane>
    </main>
  )
}

// ---- 3.4 Manifest and deploy prep ----------------------------------------------------------------------
export function ReleasePrep({ snap }: { snap: Snapshot }) {
  const wf = snap.workflow
  const m = wf?.manifest
  const p = snap.persist
  const locked = (n: 3 | 4) => (snap.steps[n - 1])!.state === 'locked' ? (snap.steps[n - 1])!.reason : undefined
  return (
    <main className="panes">
      <Pane id="left-title" kicker="Context and scope" title="Release inputs">
        <dl className="kv">
          <div><dt>SOW</dt><dd>{snap.sow?.approval ? <>v{snap.sow.approval.version} approved <Time iso={snap.sow.approval.at} /></> : 'Not approved'}</dd></div>
          <div><dt>Grid review</dt><dd>{snap.gridReviewedAt ? <Time iso={snap.gridReviewedAt} prefix="Reviewed " /> : 'Not reviewed'}</dd></div>
          <div><dt>Contract run</dt><dd>{wf ? <Copy value={wf.workflowId} label="workflow ID" /> : 'Not generated'}</dd></div>
          <div><dt>Skills</dt><dd>{m ? `${m.skills.length} in ${new Set(m.skills.map(s => s.agent)).size} agents` : '—'}</dd></div>
        </dl>
        {m && <Section title="Skills → files">
          <ul className="nav-list">{m.skills.map(s => <li key={s.id} className="file-link"><button className="nav-item" onClick={() => { setUi({ reviewSkill: s.id }); goScreen('3.3') }}>
            <span>{s.agent} › {s.name}</span></button><Copy value={s.file} display={s.file.replace('skills/', '')} label="file path" /></li>)}</ul>
        </Section>}
      </Pane>
      <Pane id="center-title" kicker="Release prep" title="Manifest and deploy prep" className="center">
        {!m ? <Empty icon="file" title="No manifest yet" action={<button className="btn btn-secondary" onClick={() => goScreen(snap.sow?.approval ? '3.2' : '3.1')}>Open {snap.sow?.approval ? '3.2' : '3.1'}</button>}>
            The manifest is written by the validation run after the ISATVON Contract is generated in the Skills grid.</Empty> : <>
          <dl className="kv inline">
            <div><dt>Version</dt><dd className="mono">{m.version}</dd></div>
            <div><dt>Hash</dt><dd><Copy value={m.hash} display={shortHash(m.hash)} label="manifest hash" /></dd></div>
            <div><dt>Updated</dt><dd><Time iso={m.createdAt} /></dd></div>
            <div><dt>From SOW</dt><dd className="mono">v{m.sowVersion}</dd></div>
          </dl>
          <Section title="Release prerequisites" aside={<DemoTag />}><CertChecklist snap={snap} /></Section>
          <Section title="Draft vs certified"><ManifestCompare snap={snap} /></Section>
        </>}
        <Section title="Hand-off into the Sandbox">
          <p className="muted sm">Validation, approval, testing, certification and freezing happen in Sandbox validation. This screen only prepares and links; it cannot bypass any of them.</p>
          <div className="row wrap">
            <GateButton variant="secondary" icon="link" reason={locked(3)} onClick={() => goScreen('4.2')}>Template</GateButton>
            <GateButton variant="secondary" icon="link" reason={locked(3)} onClick={() => goScreen('4.2')}>Approve manifest</GateButton>
            <GateButton icon="bolt" reason={locked(4)} onClick={() => goScreen('4.4')}>Test and certify</GateButton>
          </div>
        </Section>
      </Pane>
      <Pane id="right-title" kicker="Evidence and status" title="Versions and artifacts">
        <Section title="Version history"><VersionHistory snap={snap} /></Section>
        <Section title="Frozen artifacts">
          {!p.committed ? <p className="muted sm">Nothing frozen yet. A certified version is frozen when it is committed to BAM_db in 4.4.</p> : <>
            <dl className="kv"><div><dt>Frozen version</dt><dd><span className="mono">{p.committed.version}</span> · <Copy value={p.committed.hash} display={p.committed.hash.slice(0, 8)} label="frozen hash" /></dd></div>
              {p.committed.runId && <div><dt>Commit</dt><dd><Copy value={p.committed.runId} label="commit run ID" /></dd></div>}</dl>
            {p.artifacts.length > 0 && <StageList stages={p.artifacts} />}
            <DemoTag />
          </>}
        </Section>
        <Section title="Staging (separate from certification)">
          {p.deployed ? <p className="sm"><StatusPill status="succeeded" text={`Deployed ${p.deployed.version}`} /> <Time iso={p.deployed.at} /> <DemoTag /></p> : <p className="muted sm">Nothing deployed to Staging.</p>}
        </Section>
      </Pane>
    </main>
  )
}
