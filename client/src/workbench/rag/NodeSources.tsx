import { isNodeForbidden, isNodeGone, locationLabel, useKnowledgeNode } from '../lib/knowledgeNode'
import { Icon } from '../components/ui'

/** The passages an entity was read from (read only): in the graph's inspector, under the relations. */
export function NodeSources({ versionId, nodeId }: { versionId?: string; nodeId: string }) {
  const { data, error, isLoading, refetch } = useKnowledgeNode(versionId, nodeId)
  let body
  if (error) {
    body = isNodeForbidden(error) ? <p className="xs tone-err" role="alert">Your account is not assigned to this project.</p>
      : isNodeGone(error) ? <p className="muted xs">This entity is not in the knowledge version any more.</p>
      : <p className="xs tone-err" role="alert">{(error as { problem?: { detail?: string } }).problem?.detail ?? (error as { message?: string }).message ?? 'Could not load the sources.'}{' '}
          <button className="btn btn-secondary sm" onClick={() => void refetch()}>Retry</button></p>
  } else if (isLoading || !data) {
    body = <p className="muted xs" role="status"><Icon name="spinner" spin size={11} /> Loading the sources…</p>
  } else if (!data.source_passages.length) {
    body = <p className="muted xs">No source passage is recorded for this entity.</p>
  } else {
    body = <ul className="rg-rels" aria-label="Source passages">
      {data.source_passages.map((p, i) => (
        <li key={i}><strong>{p.title}</strong> <span className="muted xs">{locationLabel(p.location)}</span>
          <blockquote className="xs">{p.text}{p.truncated ? '…' : ''}</blockquote></li>
      ))}
      {data.has_more && <li className="muted xs">More entities are linked to this one than shown.</li>}
    </ul>
  }
  return <section aria-label="Sources"><h4 className="xs muted">Sources</h4>{body}</section>
}
