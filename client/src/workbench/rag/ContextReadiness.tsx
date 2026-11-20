import { useCustom } from '@refinedev/core'
import type { KnowledgeVersionList, KnowledgeVersionSummary } from '@/contracts'
import { okfBundleUrl } from '../http/sbamApi'
import { projectIdOfPath } from '../lib/evidence'
import { Banner, Copy, Empty, ErrorState, Icon, Kpis, Pane, Section, Skeleton, StatusPill, Time, shortHash } from '../components/ui'

// Context readiness (2.4, Part of #825): is the newest knowledge version ready to write a SOW from. Everything comes from
// `GET /projects/{p}/knowledge` (E5.6): the graph (entities, relations, the models that built it), retrieval (chunks
// and the anchor check: how many identifiers written in the documents became entities), each document's coverage and
// the OKF bundle. Schema readiness and the Verify questions (2.3) have no backend yet (#825); they are not shown.

type Failure = { statusCode?: number; problem?: { detail?: string }; message?: string }

const newest = (versions: KnowledgeVersionSummary[]) => [...versions].sort((a, b) => b.version - a.version)[0]

// COVERED and CHUNKS_ONLY once the bundle exists (okf.CoverageStatus), ADDED before it (knowledge.SourceStatus): only FAILED is not covered.
const isCovered = (status: string) => status !== 'FAILED'

function Ready({ v, projectId }: { v: KnowledgeVersionSummary; projectId: string | undefined }) {
  const c = v.coverage
  const anchors = c.anchors
  const rows = v.documents ?? []
  const covered = rows.filter(r => isCovered(r.status)).length
  const mock = c.extraction?.startsWith('mock|') ?? false
  const anchorTone = !anchors || anchors.total === 0 ? undefined : anchors.found === anchors.total ? 'ok' as const : 'warn' as const
  return <>
    {mock && <Banner tone="info" icon="info"><strong>Built with the mock model.</strong> The graph is synthetic; it is a test setup.</Banner>}
    <Section title="Graph">
      <Kpis label="Graph readiness" items={[
        { value: v.counts?.entities ?? 0, label: 'Entities' },
        { value: v.counts?.relations ?? 0, label: 'Relations' },
        { value: c.masters_included, label: 'Master entries' },
      ]} />
      {c.extraction && <p className="muted xs">Built by <span className="mono">{c.extraction}</span></p>}
    </Section>
    <Section title="Retrieval">
      <Kpis label="Retrieval readiness" items={[
        { value: v.counts?.chunks ?? 0, label: 'Chunks' },
        { value: anchors ? `${anchors.found} / ${anchors.total}` : '—', label: 'Identifiers found', tone: anchorTone },
      ]} />
      {anchors?.missing && anchors.missing.length > 0 && <p className="muted xs">Not found as entities: {anchors.missing.join(', ')}</p>}
    </Section>
    <Section title="Documents" aside={<span className="muted sm">{covered} of {rows.length} covered</span>}>
      {rows.length === 0 ? <p className="muted sm">No documents in this version.</p> : (
        <table className="table" aria-label="Document coverage">
          <thead><tr><th scope="col">Document</th><th scope="col">Status</th><th scope="col">Chunks</th><th scope="col">Entities</th></tr></thead>
          <tbody>{rows.map(r => (
            <tr key={r.document_version_id}>
              <td>{r.title}</td>
              <td><StatusPill status={isCovered(r.status) ? 'pass' : 'fail'} text={r.reason ? `${r.status} · ${r.reason}` : r.status} /></td>
              <td>{r.chunks}</td><td>{r.entities}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Section>
    <Section title="Knowledge bundle (OKF)">
      {v.okf_sha256 ? <dl className="kv">
        <div><dt>Bundle hash</dt><dd><Copy value={v.okf_sha256} display={shortHash(v.okf_sha256.replace(/^sha256:/, ''))} label="bundle hash" /></dd></div>
        {projectId && <div><dt>Download</dt><dd><a className="btn btn-secondary sm" href={okfBundleUrl(projectId, v.id)}><Icon name="download" />OKF bundle</a></dd></div>}
      </dl> : <p className="muted sm">The bundle is written once the version is ready.</p>}
    </Section>
  </>
}

/** 2.4: read only; there is nothing to approve here (the SOW step starts from the ready version). */
export function ContextReadiness() {
  const projectId = typeof window === 'undefined' ? undefined : projectIdOfPath(window.location.pathname)
  const { query } = useCustom<KnowledgeVersionList>({ url: 'knowledge-versions', method: 'get', queryOptions: { retry: false } })
  const data = query.data?.data
  const v = data ? newest(data.versions) : undefined
  let body
  if (query.error) {
    const f = query.error as Failure
    body = f.statusCode === 403 ? <ErrorState title="You can't see this project's knowledge">Your account is not assigned to this project.</ErrorState>
      : <ErrorState title="Could not load the knowledge" action={<button className="btn btn-primary" onClick={() => query.refetch()}>Retry</button>}>{f.problem?.detail ?? f.message ?? 'The request failed.'}</ErrorState>
  } else if (query.isLoading || !data) body = <Skeleton rows={6} label="Loading the knowledge" />
  else if (!v) body = <Empty icon="info" title="No knowledge yet">The knowledge graph is built once the uploaded files are read.</Empty>
  else if (v.status === 'FAILED') body = <Banner tone="err" icon="x"><strong>Version {v.version} failed.</strong> {v.error_reason ?? 'No reason reported.'} Rebuild it from Index.</Banner>
  else if (v.status !== 'READY') body = <Banner tone="info" icon="refresh"><strong>Version {v.version} is building.</strong> Readiness shows once it is ready.</Banner>
  else body = <Ready v={v} projectId={projectId} />
  return (
    <Pane id="center-title" kicker="Context" title="Context readiness" className="center"
      actions={v ? <span className="mono xs muted">version {v.version}{v.finished_at && <> · <Time iso={v.finished_at} /></>}</span> : undefined}>
      {body}
    </Pane>
  )
}
