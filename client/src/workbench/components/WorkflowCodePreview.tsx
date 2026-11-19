import { useEffect, useMemo, useRef, useState } from 'react'
import type { CodeFinding, FileTag, WorkflowCodeGroup } from '@/contracts'
import { fileCount, findingText, firstFile, groupHasProblem, isForbidden, placeFindings, splitLines, statusTone, tagCounts, TAG_HINT, TAG_LABEL, TAGS, useWorkflowCode, useWorkflowFile } from '../lib/workflowCode'
import { RegistryPanel } from './RegistryPanel'
import { RepositoryPanel } from './RepositoryPanel'
import { RepoQuestionCard } from './RepoQuestionCard'
import { EVIDENCE_VIEWS, EvidenceBody, isEvidenceView, type EvidenceView } from './BuildEvidence'
import { Banner, Copy, Empty, ErrorState, Icon, Pane, Skeleton } from './ui'
import './workflowCode.css'

// Translate (4.3), the whole workflow's code, read only (#841): one group per Sub-Skill plus the workflow's own
// files (the vendored repository, patches, schema and manifest), a tag on every file, the gate results beside the
// files and the findings on their lines. It reads the stored code from the SBAM API and offers no way to change it.

export function TagPill({ tag }: { tag: FileTag }) {
  return <span className={`pill wcp-tag wcp-tag-${tag}`} title={TAG_HINT[tag]}>{TAG_LABEL[tag]}</span>
}

const dirOf = (path: string) => { const i = path.lastIndexOf('/'); return i < 0 ? '' : path.slice(0, i + 1) }
const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1)

function Forbidden() {
  return <ErrorState title="You can't see this workflow's code">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}

function GroupHeader({ g }: { g: WorkflowCodeGroup }) {
  return (
    <header className="wcp-group-head">
      <h4>{g.label}</h4>
      {g.status && <span className={`pill tone-${statusTone(g.status)}`}>{g.status}</span>}
      {g.gates.length > 0 && (
        <ul className="wcp-gates" aria-label={`Gate results for ${g.label}`}>
          {g.gates.map(x => <li key={x.gate}><span className={`pill tone-${statusTone(x.status)}`} title={`${x.gate}: ${x.status}`}>{x.gate} · {x.status}</span></li>)}
        </ul>
      )}
    </header>
  )
}

function FileList({ groups, current, onSelect }: { groups: WorkflowCodeGroup[]; current?: { group: string; path: string }; onSelect: (group: string, path: string) => void }) {
  return (
    <nav className="wcp-side" aria-label="Workflow files">
      {groups.map(g => (
        <section key={g.group_id} className="wcp-group" aria-label={g.label}>
          <GroupHeader g={g} />
          {g.findings.length > 0 && (
            <ul className="wcp-loose" aria-label={`Findings without a file in ${g.label}`}>
              {g.findings.map((f, i) => <li key={i}><Icon name="alert" size={12} />{findingText(f)}</li>)}
            </ul>
          )}
          {g.files.length === 0
            ? <p className="wcp-none">{g.kind === 'workflow' ? 'No workflow-level files are stored yet.' : 'No code stored for this attempt.'}</p>
            : (
              <ul className="wcp-files">
                {g.files.map(f => {
                  const on = current?.group === g.group_id && current.path === f.path
                  return (
                    <li key={f.path}>
                      <button type="button" className={`wcp-file ${on ? 'on' : ''}`} aria-current={on ? 'true' : undefined} title={f.path}
                        onClick={() => onSelect(g.group_id, f.path)}>
                        <Icon name="file" size={13} />
                        <span className="wcp-path"><span className="wcp-dir">{dirOf(f.path)}</span>{nameOf(f.path)}</span>
                        {f.findings.length > 0 && <span className="wcp-count tone-err" aria-label={`${f.findings.length} finding${f.findings.length === 1 ? '' : 's'}`}>{f.findings.length}</span>}
                        <TagPill tag={f.tag} />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
        </section>
      ))}
    </nav>
  )
}

function FileViewer({ workflowId, group, path }: { workflowId: string; group: string; path: string }) {
  const { file, error, isLoading, refetch } = useWorkflowFile(workflowId, group, path)
  const [focus, setFocus] = useState<number>()
  const lineRef = useRef<HTMLLIElement | null>(null)
  useEffect(() => { setFocus(undefined) }, [group, path])
  useEffect(() => { lineRef.current?.scrollIntoView?.({ block: 'center' }) }, [focus])
  const lines = useMemo(() => splitLines(file?.content ?? ''), [file?.content])
  const { byLine } = useMemo(() => placeFindings(file?.findings ?? [], lines.length), [file?.findings, lines.length])
  if (error) return isForbidden(error) ? <Forbidden />
    : <ErrorState title="Could not load this file" action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{error.message}</ErrorState>
  if (isLoading || !file) return <Skeleton rows={8} label="Loading the file" />
  return (
    <div className="wcp-viewer">
      <header className="wcp-view-head">
        <h4 className="mono" title={file.path}>{file.path}</h4>
        <TagPill tag={file.tag} />
        <Copy value={file.sha256} display={file.sha256.slice(0, 12)} label="file hash" />
        <span className="wcp-readonly" title="This view cannot change the code"><Icon name="lock" size={12} />Read only</span>
      </header>
      {file.truncated && <Banner tone="warn" icon="alert">This file is longer than 1 MiB. Only the first part is shown.</Banner>}
      {file.findings.length > 0 && (
        <ul className="wcp-findings" aria-label="Findings in this file">
          {file.findings.map((f: CodeFinding, i) => (
            <li key={i}>
              <Icon name="alert" size={12} />
              <span>{findingText(f)}</span>
              {f.line && f.line <= lines.length
                ? <button type="button" className="link xs" onClick={() => setFocus(f.line!)}>line {f.line}</button>
                : f.line ? <span className="wcp-faint">line {f.line} is past the shown text</span> : null}
            </li>
          ))}
        </ul>
      )}
      <ol className="wcp-code" aria-label={`Content of ${file.path}`}>
        {lines.map((text, i) => {
          const n = i + 1, marks = byLine.get(n)
          return (
            <li key={n} ref={focus === n ? lineRef : undefined} className={`${marks ? 'flag' : ''} ${focus === n ? 'focus' : ''}`}>
              <span className="wcp-gutter" title={marks?.map(findingText).join('\n')}>{marks ? <Icon name="alert" size={11} label={`${marks.length} finding${marks.length === 1 ? '' : 's'} on line ${n}`} /> : null}</span>
              <span className="wcp-ln" aria-hidden>{n}</span>
              <code>{text || ' '}</code>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function WorkflowCodePreview({ workflowId }: { workflowId?: string }) {
  const { tree, error, isLoading, refetch } = useWorkflowCode(workflowId)
  const [sel, setSel] = useState<{ group: string; path: string }>()
  const current = useMemo(() => {
    if (!tree) return undefined
    const exists = sel && tree.groups.some(g => g.group_id === sel.group && g.files.some(f => f.path === sel.path))
    return exists ? sel : firstFile(tree)
  }, [tree, sel])
  if (!workflowId) return <Empty icon="code" title="No workflow yet">The code appears here once the build has started.</Empty>
  if (error) return isForbidden(error) ? <Forbidden />
    : <ErrorState title="Could not load the workflow's code" action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{error.message}</ErrorState>
  if (isLoading || !tree) return <Skeleton rows={8} label="Loading the workflow's code" />
  if (fileCount(tree) === 0 && tree.groups.every(g => g.findings.length === 0 && g.gates.length === 0)) {
    return <Empty icon="code" title="No code stored yet">The build stores each Sub-Skill&apos;s code when its attempt finishes. Nothing is stored for this workflow so far.</Empty>
  }
  const counts = tagCounts(tree)
  const problems = tree.groups.filter(groupHasProblem).length
  return (
    <div className="wcp" data-testid="workflow-code">
      <div className="wcp-summary" role="group" aria-label="Files by origin">
        <strong>{fileCount(tree)} file{fileCount(tree) === 1 ? '' : 's'}</strong>
        {TAGS.map(t => <span key={t} className={`pill wcp-tag wcp-tag-${t}`} title={TAG_HINT[t]}>{TAG_LABEL[t]} {counts[t]}</span>)}
        {problems > 0 && <span className="pill tone-err">{problems} group{problems === 1 ? '' : 's'} with findings</span>}
      </div>
      <div className="wcp-body">
        <FileList groups={tree.groups} current={current} onSelect={(group, path) => setSel({ group, path })} />
        {current
          ? <FileViewer workflowId={workflowId} group={current.group} path={current.path} />
          : <Empty icon="file" title="No file selected">The groups above have findings but no stored files.</Empty>}
      </div>
    </div>
  )
}

/** The Translate centre when the workbench has no validation run of its own: the stored code, read only. */
export function WorkflowFilesCenter({ workflowId }: { workflowId?: string }) {
  const [view, setView] = useState<'files' | 'repos' | 'registry' | EvidenceView>('files')
  return (
    <Pane id="center-title" kicker="Translation" title="Translate" className="center"
      actions={<CodeViewSwitch view={view} set={v => setView(v === 'repos' || v === 'registry' || isEvidenceView(v) ? v : 'files')} runless />}>
      {workflowId && <RepoQuestionCard workflowId={workflowId} quiet={view !== 'repos'} />}
      {view === 'repos' ? <RepositoryPanel workflowId={workflowId} />
        : view === 'registry' ? <RegistryPanel workflowId={workflowId} />
        : isEvidenceView(view) ? <EvidenceBody view={view} workflowId={workflowId} />
        : <WorkflowCodePreview workflowId={workflowId} />}
    </Pane>
  )
}

export type CodeView = 'run' | 'files' | 'repos' | 'registry' | EvidenceView

/** Switches Translate between the run's code, the whole workflow's stored files, its repositories (#843) and its registry entry (#849). */
export function CodeViewSwitch({ view, set, runless }: { view: CodeView; set: (v: CodeView) => void; runless?: boolean }) {
  return (
    <div className="wcp-switch" role="group" aria-label="Code view">
      {!runless && <button type="button" aria-pressed={view === 'run'} onClick={() => set('run')}>Run code</button>}
      <button type="button" aria-pressed={view === 'files'} onClick={() => set('files')}>Workflow files</button>
      <button type="button" aria-pressed={view === 'repos'} onClick={() => set('repos')}>Repositories</button>
      <button type="button" aria-pressed={view === 'registry'} onClick={() => set('registry')}>Registry</button>
      {EVIDENCE_VIEWS.map(e => <button key={e.id} type="button" aria-pressed={view === e.id} onClick={() => set(e.id)}>{e.label}</button>)}
    </div>
  )
}
