// Certification evidence (#842): the import gate, repository licence, dependency vulnerability and MCP compliance
// results #761 records on each Sub-Skill's bundle hash, read from the build. Read-only; a result that is not a pass
// says so and that it blocks certification. Shown in the Check and Certify screens while the rest of those screens is
// not wired to the backend yet.
import { useQuery } from '@tanstack/react-query'
import type { BuildsResponse, GatesResponse } from '@/contracts'
import { guardPoll } from '@/lib/session-ended'
import { Banner, Empty, Icon, Section, Skeleton, StatusPill } from '../components/ui'
import { ApiFailure, getJson } from '../http/http'
import { buildsPath, certificationBlocked, evidenceRows, gatesPath, reasonsOf, type EvidenceRow, type EvidenceState, type SubRef } from '../lib/evidence'
import './cert-evidence.css'

const PILL: Record<EvidenceState, 'pass' | 'fail' | 'pending' | 'running'> = { pass: 'pass', fail: 'fail', running: 'running', missing: 'pending' }
const PILL_TEXT: Record<EvidenceState, string> = { pass: 'Pass', fail: 'Fail', running: 'Running', missing: 'Missing' }
const SHOWN = 5

/** The server's own wording when it gave one (problem+json detail), else the failure's message. */
const messageOf = (e: unknown): string => {
  const f = e as ApiFailure
  return f.problem?.detail ?? f.message ?? 'The evidence could not be loaded.'
}

function Names({ subs }: { subs: SubRef[] }) {
  const shown = subs.slice(0, SHOWN)
  return <>{shown.map(s => s.skillName).join(', ')}{subs.length > shown.length ? ` and ${subs.length - shown.length} more` : ''}</>
}

// One failing Sub-Skill and what its gate found, read from that Sub-Skill's gates (the latest attempt).
function Why({ projectId, workflowId, sub, row }: { projectId: string; workflowId: string; sub: SubRef; row: EvidenceRow }) {
  const q = useQuery({
    queryKey: ['cert-evidence-gates', projectId, workflowId, sub.subSkillId],
    queryFn: () => getJson<GatesResponse>(gatesPath(projectId, workflowId, sub.subSkillId)).then(r => r.body),
    retry: false,
  })
  const reasons = reasonsOf(q.data?.gates.find(g => g.gate === row.gate)?.findings, row.gate)
  return (
    <li>
      <strong>{sub.skillName}</strong>
      {q.isLoading ? <span className="muted xs"> · loading the reason…</span>
        : q.error ? <span className="muted xs"> · the reason could not be loaded</span>
          : reasons.length ? <ul className="ce-why">{reasons.map((m, i) => <li key={i}>{m}</li>)}</ul>
            : <span className="muted xs"> · no reason was recorded</span>}
    </li>
  )
}

function Row({ row, projectId, workflowId }: { row: EvidenceRow; projectId: string; workflowId: string }) {
  return (
    <li className={`cert-item cs-${row.state === 'missing' ? 'pending' : row.state}`} data-gate={row.gate}>
      <StatusPill status={PILL[row.state]} text={PILL_TEXT[row.state]} />
      <div className="cert-main">
        <span className="cert-label">{row.label}</span>
        <span className="cert-detail">{row.detail}</span>
        {row.state === 'pass' && <span className="cert-detail">Passed for all {row.total} Sub-Skills.</span>}
        {row.state === 'running' && <span className="cert-detail">Running: {row.passed} of {row.total} Sub-Skills have passed so far.</span>}
        {row.state === 'missing' && <span className="cert-detail">{row.total ? <>No result yet for <Names subs={row.missing} />. Certification is blocked until it passes.</> : 'No Sub-Skill has been built yet. Certification is blocked.'}</span>}
        {row.state === 'fail' && <ul className="ce-subs">{row.failing.slice(0, SHOWN).map(s => <Why key={s.subSkillId} projectId={projectId} workflowId={workflowId} sub={s} row={row} />)}
          {row.failing.length > SHOWN && <li className="muted xs">and {row.failing.length - SHOWN} more Sub-Skills</li>}</ul>}
      </div>
    </li>
  )
}

export function CertEvidence({ projectId, workflowId }: { projectId: string; workflowId: string }) {
  const q = useQuery({
    queryKey: ['cert-evidence', projectId, workflowId],
    queryFn: () => getJson<BuildsResponse>(buildsPath(projectId, workflowId)).then(r => r.body),
    refetchInterval: guardPoll((query: { state: { data?: BuildsResponse; error: unknown } }) => (query.state.data?.active ? 4000 : false)),
    retry: false,
  })
  const status = (q.error as ApiFailure | null)?.statusCode
  const subs = q.data?.sub_skills ?? []
  const rows = evidenceRows(subs)
  const blocked = certificationBlocked(rows)
  let body
  if (q.isLoading) body = <Skeleton rows={4} label="Loading the certification evidence" />
  else if (status === 403 || status === 404) body = <Empty icon="lock" title="Not available">You cannot see the certification evidence of this workflow.</Empty>
  else if (q.error) body = <Banner tone="err" icon="x" action={<button className="btn btn-ghost sm" onClick={() => void q.refetch()}>Retry</button>}><strong>The evidence could not be loaded.</strong> {messageOf(q.error)}</Banner>
  else if (!subs.length) body = <Empty icon="tb-certificate" title="No evidence yet">No Sub-Skill has been built for this workflow yet, so there is nothing to certify.</Empty>
  else body = <>
    <ol className="cert-list">{rows.map(r => <Row key={r.gate} row={r} projectId={projectId} workflowId={workflowId} />)}</ol>
    <p className="muted sm ce-note"><Icon name={blocked ? 'lock' : 'check'} size={13} /> {blocked ? 'Certification is blocked until all four results pass for every Sub-Skill, on the same bundle hash.' : 'All four results pass for every Sub-Skill. Certification can be recorded.'}</p>
  </>
  const aside = subs.length && !q.error ? <StatusPill status={blocked ? 'fail' : 'pass'} text={blocked ? 'Blocks certification' : 'Ready'} /> : undefined
  return <div className="cert-evidence"><Section title="Certification evidence" aside={aside}>{body}</Section></div>
}
