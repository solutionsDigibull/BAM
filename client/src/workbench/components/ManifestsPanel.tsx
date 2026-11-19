import { useMemo, useState } from 'react'
import type { ManifestVersion } from '@/contracts'
import { hashOf, isForbidden, originOf, pretty, problemText, useManifest, useManifestVersions, versionKey, type Subject } from '../lib/manifests'
import { useBuilds } from '../lib/workflowBuilds'
import { Banner, Copy, Empty, ErrorState, Icon, Skeleton, Time } from './ui'
import './manifestsPanel.css'

// Translate (4.3), the manifests of the workflow and of each Sub-Skill, read only (#715): the versions stored, newest first,
// and one manifest as stored. The JSON is shown as text with sorted keys. Saving and approving a manifest have no backend
// and are not offered here.

function Forbidden() {
  return <ErrorState title="You can't see these manifests">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}
const retryAction = (refetch: () => unknown) => <button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>
const WORKFLOW = '__workflow__'

function Viewer({ workflowId, subject, version }: { workflowId: string; subject: Subject; version: ManifestVersion }) {
  const { view, error, isLoading, refetch } = useManifest(workflowId, subject, version)
  const text = useMemo(() => (view ? pretty(view.manifest) : ''), [view])
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load this manifest" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !view) return <Skeleton rows={8} label="Loading the manifest" />
  return (
    <div className="mf-viewer">
      <header className="mf-view-head">
        <h4 className="mono">v{view.version}</h4>
        <span className="muted xs">schema {view.schema_version}</span>
        {view.is_frozen && <span className="pill tone-ok" title="A frozen version never changes"><Icon name="lock" size={11} />Frozen</span>}
        {view.reused && <span className="pill tone-muted" title="The Sub-Skill was reused, or its hash equals the build before">Reused</span>}
        <Copy value={hashOf(view)} display={hashOf(view).replace(/^sha256:/, '').slice(0, 12)} label="manifest hash" />
        <span className="mf-readonly" title="This view cannot change the manifest"><Icon name="lock" size={12} />Read only</span>
      </header>
      <pre className="mf-json" tabIndex={0} aria-label={`Manifest v${view.version}`}>{text}</pre>
    </div>
  )
}

function Versions({ workflowId, subject }: { workflowId: string; subject: Subject }) {
  const { items, error, isLoading, refetch } = useManifestVersions(workflowId, subject)
  const [key, setKey] = useState<string>()
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load the manifests" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !items) return <Skeleton rows={4} label="Loading the manifests" />
  if (items.length === 0) return <Empty icon="file" title="Not generated yet">The build writes a manifest when it generates {subject ? 'this Sub-Skill' : 'the workflow'}.</Empty>
  const current = items.find(v => versionKey(v) === key) ?? items[0]!
  return (
    <div className="mf-body">
      <ul className="mf-versions" aria-label="Versions">
        {items.map(v => (
          <li key={versionKey(v)}>
            <button type="button" className={`mf-version ${v === current ? 'on' : ''}`} aria-pressed={v === current} onClick={() => setKey(versionKey(v))}>
              <strong className="mono">v{v.version}</strong>
              {v.is_frozen && <span className="pill tone-ok">Frozen</span>}
              <span className="muted xs">{originOf(v)}</span>
              <span className="muted xs"><Time iso={v.created_at} /></span>
            </button>
          </li>
        ))}
      </ul>
      <Viewer workflowId={workflowId} subject={subject} version={current} />
    </div>
  )
}

/** The panel body: pick the workflow or a Sub-Skill, then its versions. Loading, empty, error and forbidden are shown. */
export function ManifestsPanel({ workflowId }: { workflowId?: string }) {
  const builds = useBuilds(workflowId)
  const [pick, setPick] = useState<string>(WORKFLOW)
  if (!workflowId) return <Empty icon="file" title="No manifests yet">The manifests appear here once the build has started.</Empty>
  if (builds.error) return isForbidden(builds.error) ? <Forbidden /> : <ErrorState title="Could not load the Sub-Skills" action={retryAction(builds.refetch)}>{problemText(builds.error)}</ErrorState>
  const rows = builds.data?.sub_skills ?? []
  const subject = pick === WORKFLOW || !rows.some(r => r.sub_skill_id === pick) ? undefined : pick
  return (
    <div className="mf" aria-label="Manifests" data-testid="manifests">
      <div className="mf-pick">
        <label className="xs muted" htmlFor="mf-subject">Manifest of</label>
        <select id="mf-subject" value={subject ?? WORKFLOW} onChange={e => setPick(e.target.value)}>
          <option value={WORKFLOW}>The workflow</option>
          {rows.map(r => <option key={r.sub_skill_id} value={r.sub_skill_id}>{r.skill_name} ({r.skill_id})</option>)}
        </select>
      </div>
      <Versions key={subject ?? WORKFLOW} workflowId={workflowId} subject={subject} />
      <Banner tone="info" icon="lock">Read only. The build writes the manifest; it is changed by retrying the build, never by editing it here.</Banner>
    </div>
  )
}
