import { useEffect, useMemo, useState } from 'react'
import type { CodeAttemptSummary } from '@/contracts'
import {
  attemptLabel, comparable, diffKind, inSlot, isForbidden, newestFirst, problemText, useAttemptDiff, useAttemptFile, useAttempts,
} from '../lib/codeAttempts'
import { placeFindings, splitLines, statusTone } from '../lib/workflowCode'
import { useBuilds } from '../lib/workflowBuilds'
import { Banner, Copy, Empty, ErrorState, Icon, Skeleton } from './ui'
import './attemptsPanel.css'

// Translate (4.3), the code attempts of one Sub-Skill, read only (#715): attempt 0 is the first generation, 1 and 2 are
// the corrections. Each attempt says what it produced; a file shows which lines are the generated body (the rest is the
// protected template) with its findings on their lines; a correction can be compared with another attempt line by line.

function Forbidden() {
  return <ErrorState title="You can't see this Sub-Skill's code">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}
const retryAction = (refetch: () => unknown) => <button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>

function FileView({ workflowId, subId, attempt, path, compareTo }: { workflowId: string; subId: string; attempt: number; path: string; compareTo?: number }) {
  const { file, error, isLoading, refetch } = useAttemptFile(workflowId, subId, attempt, path)
  const diff = useAttemptDiff(workflowId, subId, compareTo, attempt, compareTo === undefined ? undefined : path)
  const lines = useMemo(() => splitLines(file?.content ?? ''), [file?.content])
  const { byLine } = useMemo(() => placeFindings(file?.findings ?? [], lines.length), [file?.findings, lines.length])
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load this file" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !file) return <Skeleton rows={8} label="Loading the file" />
  return (
    <div className="att-viewer">
      <header className="att-view-head">
        <h4 className="mono" title={file.path}>{file.path}</h4>
        <Copy value={file.sha256} display={file.sha256.slice(0, 12)} label="file hash" />
        <span className="att-readonly" title="This view cannot change the code"><Icon name="lock" size={12} />Read only</span>
      </header>
      {file.truncated && <Banner tone="warn" icon="alert">This file is longer than 1 MiB. Only the first part is shown.</Banner>}
      {compareTo !== undefined && (
        diff.error ? <ErrorState title="Could not load the comparison" action={retryAction(diff.refetch)}>{problemText(diff.error)}</ErrorState>
          : diff.isLoading || !diff.diff ? <Skeleton rows={4} label="Loading the comparison" />
            : !diff.diff.changed ? <Banner tone="info" icon="check">This file is the same in {attemptLabel(compareTo).toLowerCase()} and {attemptLabel(attempt).toLowerCase()}.</Banner>
              : (
                <figure className="att-diff" aria-label={`Changes from ${attemptLabel(compareTo).toLowerCase()} to ${attemptLabel(attempt).toLowerCase()}`}>
                  <pre>{diff.diff.unified.split('\n').map((l, i) => <span key={i} className={`att-d-${diffKind(l)}`}>{l || ' '}{'\n'}</span>)}</pre>
                  {diff.diff.truncated && <figcaption className="muted xs">The comparison is cut at its size limit.</figcaption>}
                </figure>
              )
      )}
      <p className="muted xs"><span className="att-key att-slot" />Generated body <span className="att-key att-protected" />Protected template</p>
      <ol className="wcp-code att-code" aria-label={`Content of ${file.path}`}>
        {lines.map((text, i) => {
          const n = i + 1, marks = byLine.get(n), slot = inSlot(n, file.slots)
          return (
            <li key={n} className={`${slot ? 'att-slot-line' : 'att-protected-line'} ${marks ? 'flag' : ''}`}>
              <span className="wcp-gutter" title={marks?.map(f => `${f.code}: ${f.message}`).join('\n')}>{marks ? <Icon name="alert" size={11} label={`${marks.length} finding${marks.length === 1 ? '' : 's'} on line ${n}`} /> : null}</span>
              <span className="wcp-ln" aria-hidden>{n}</span>
              <code>{text || ' '}</code>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function AttemptCard({ a, on, onPick }: { a: CodeAttemptSummary; on: boolean; onPick: () => void }) {
  return (
    <li>
      <button type="button" className={`att-card ${on ? 'on' : ''}`} aria-pressed={on} onClick={onPick}>
        <span className="att-card-head"><strong>{attemptLabel(a.attempt)}</strong><span className={`pill tone-${statusTone(a.status)}`}>{a.status}</span></span>
        <span className="muted xs">{a.template_name} {a.template_version} · {a.files.length} file{a.files.length === 1 ? '' : 's'} · {a.findings.length} finding{a.findings.length === 1 ? '' : 's'}</span>
        {a.correction_reason && <span className="att-reason xs">{a.correction_reason}</span>}
      </button>
    </li>
  )
}

function Attempts({ workflowId, subId }: { workflowId: string; subId: string }) {
  const { items, error, isLoading, refetch } = useAttempts(workflowId, subId)
  const [attempt, setAttempt] = useState<number>()
  const [path, setPath] = useState<string>()
  const [compare, setCompare] = useState<number>()
  const list = useMemo(() => newestFirst(items ?? []), [items])
  const current = list.find(a => a.attempt === attempt) ?? list[0]
  useEffect(() => { setAttempt(undefined); setPath(undefined); setCompare(undefined) }, [subId])
  if (error) return isForbidden(error) ? <Forbidden /> : <ErrorState title="Could not load the code attempts" action={retryAction(refetch)}>{problemText(error)}</ErrorState>
  if (isLoading || !items) return <Skeleton rows={5} label="Loading the code attempts" />
  if (list.length === 0 || !current) return <Empty icon="code" title="No code attempts yet">The build stores an attempt when it generates the Sub-Skill's code.</Empty>
  const file = current.files.find(f => f.path === path) ?? current.files[0]
  const others = comparable(list, current.attempt)
  const cmp = others.some(o => o.attempt === compare) ? compare : undefined
  return (
    <div className="att-body">
      <ul className="att-list" aria-label="Attempts">{list.map(a => <AttemptCard key={a.attempt} a={a} on={a.attempt === current.attempt} onPick={() => { setAttempt(a.attempt); setPath(undefined); setCompare(undefined) }} />)}</ul>
      {current.findings.length > 0 && (
        <ul className="att-findings" aria-label={`Findings of ${attemptLabel(current.attempt).toLowerCase()}`}>
          {current.findings.map((f, i) => <li key={i}><Icon name="alert" size={12} /><span>{f.gate} <code>{f.code}</code> {f.message}</span>{f.file && <span className="mono xs muted">{f.file}{f.line ? `:${f.line}` : ''}</span>}</li>)}
        </ul>
      )}
      {current.files.length === 0
        ? <Empty icon="file" title="No code stored for this attempt">The attempt failed before its files were assembled, so nothing was stored.</Empty>
        : (
          <>
            <div className="att-controls">
              <label className="xs muted" htmlFor="att-file">File</label>
              <select id="att-file" value={file!.path} onChange={e => setPath(e.target.value)}>{current.files.map(f => <option key={f.path} value={f.path}>{f.path}</option>)}</select>
              {others.length > 0 && <>
                <label className="xs muted" htmlFor="att-cmp">Compare with</label>
                <select id="att-cmp" value={cmp ?? ''} onChange={e => setCompare(e.target.value === '' ? undefined : Number(e.target.value))}>
                  <option value="">Nothing</option>{others.map(o => <option key={o.attempt} value={o.attempt}>{attemptLabel(o.attempt)}</option>)}
                </select>
              </>}
            </div>
            <FileView workflowId={workflowId} subId={subId} attempt={current.attempt} path={file!.path} compareTo={cmp} />
          </>
        )}
    </div>
  )
}

/** The panel body: pick a Sub-Skill of the workflow, then its attempts. Loading, empty, error and forbidden are shown. */
export function AttemptsPanel({ workflowId }: { workflowId?: string }) {
  const builds = useBuilds(workflowId)
  const [sub, setSub] = useState<string>()
  if (!workflowId) return <Empty icon="code" title="No code attempts yet">The attempts appear here once the build has started.</Empty>
  if (builds.error) return isForbidden(builds.error) ? <Forbidden /> : <ErrorState title="Could not load the Sub-Skills" action={retryAction(builds.refetch)}>{problemText(builds.error)}</ErrorState>
  if (builds.isLoading || !builds.data) return <Skeleton rows={5} label="Loading the Sub-Skills" />
  const rows = builds.data.sub_skills
  if (rows.length === 0) return <Empty icon="code" title="Nothing is building">This workflow has no Sub-Skills to show code for yet.</Empty>
  const id = rows.some(r => r.sub_skill_id === sub) ? sub! : rows[0]!.sub_skill_id
  return (
    <div className="att" aria-label="Code attempts" data-testid="attempts">
      <div className="att-pick">
        <label className="xs muted" htmlFor="att-sub">Sub-Skill</label>
        <select id="att-sub" value={id} onChange={e => setSub(e.target.value)}>{rows.map(r => <option key={r.sub_skill_id} value={r.sub_skill_id}>{r.skill_name} ({r.skill_id})</option>)}</select>
      </div>
      <Attempts workflowId={workflowId} subId={id} />
      <p className="muted xs">Read only. The code is changed by retrying the build, never by editing it here.</p>
    </div>
  )
}
