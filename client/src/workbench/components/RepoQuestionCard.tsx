import { useRef, useState } from 'react'
import type { PendingRepoQuestion, RepoCandidate } from '@/contracts'
import {
  candidates, isForbidden, KIND_HELP, KIND_TITLE, looksLikeRepoUrl, newAnswerKey, percent, problemText, reasonsOf, repoName, scoreTone,
  sendAnswer, SOURCE_LABEL, useRepoQuestions,
} from '../lib/repoQuestion'
import { questionCardId } from '../lib/workflowBuilds'
import { Banner, Empty, ErrorState, Icon, Skeleton } from './ui'
import './repoQuestion.css'

// The choose-a-repository question (#756, ADR 0028): the build is paused because the Admin catalogue has no clear
// winner for a Sub-Skill. The FDE picks one of the short list, says none fits (SBAM writes the code), or, when the
// catalogue had nothing, names a repository of their own. The server checks the answer and records who and when.
// `quiet` is for the code views: the card shows only while a question waits; the Repositories view shows every state.

function Forbidden() {
  return <ErrorState title="You can't answer for this workflow">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}

function Option({ c, busy, onPick }: { c: RepoCandidate; busy: boolean; onPick: (url: string) => void }) {
  const reasons = reasonsOf(c)
  return (
    <li className="rq-option" data-repo={c.url}>
      <div className="rq-option-head">
        <strong className="rq-name">{repoName(c.url)}</strong>
        <span className={`pill tone-${scoreTone(c.score)}`} title="How well the repo scout thinks it fits this Sub-Skill">{percent(c.score)} match</span>
        <span className="muted xs">{SOURCE_LABEL[c.source] ?? c.source}</span>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onPick(c.url)}
          aria-label={`Use ${repoName(c.url)}`}>Use this repository</button>
      </div>
      {reasons.length > 0 && <ul className="rq-reasons" aria-label={`Why ${repoName(c.url)} fits`}>{reasons.map(r => <li key={r}>{r}</li>)}</ul>}
    </li>
  )
}

function Question({ item, workflowId, onDone }: { item: PendingRepoQuestion; workflowId: string; onDone: () => void }) {
  const q = item.question
  const key = useRef(newAnswerKey())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [sent, setSent] = useState(false)
  const [own, setOwn] = useState('')
  const list = candidates(item)
  const name = item.sub_skill_name ?? 'this Sub-Skill'

  const answer = async (body: { action: 'CHOOSE'; url: string } | { action: 'NO_REPO' }) => {
    setBusy(true)
    setError(undefined)
    try {
      await sendAnswer(workflowId, q.sub_skill_id, body, key.current)
      setSent(true)
      onDone()
    } catch (e) {
      setError(problemText(e))
      setBusy(false)
    }
  }

  return (
    <section className="rq-card" id={questionCardId(q.sub_skill_id)} tabIndex={-1} aria-label={`Repository for ${name}`} data-sub-skill={q.sub_skill_id}>
      <header className="rq-head">
        <Icon name="alert" />
        <div>
          <h4>Which repository should {name} start from?</h4>
          <p className="muted xs">{KIND_TITLE[q.kind]}. The build is paused until you answer; there is no time limit, and you are reminded every 3 days.</p>
        </div>
      </header>
      <p className="rq-help">{KIND_HELP[q.kind]}</p>
      {sent ? (
        <Banner tone="ok" icon="check">Answer sent. The build carries on and records your choice.</Banner>
      ) : (
        <>
          {list.length > 0 && <ul className="rq-options" aria-label="Short list">{list.map(c => <Option key={c.url} c={c} busy={busy} onPick={url => answer({ action: 'CHOOSE', url })} />)}</ul>}
          {q.kind === 'NO_CANDIDATE' && (
            <form className="rq-own" onSubmit={e => { e.preventDefault(); if (looksLikeRepoUrl(own)) void answer({ action: 'CHOOSE', url: own.trim() }) }}>
              <label htmlFor={`rq-own-${q.sub_skill_id}`}>Your repository (https address)</label>
              <div className="rq-own-row">
                <input id={`rq-own-${q.sub_skill_id}`} type="url" inputMode="url" placeholder="https://github.com/owner/repo" value={own}
                  onChange={e => setOwn(e.target.value)} disabled={busy} />
                <button type="submit" className="btn btn-primary" disabled={busy || !looksLikeRepoUrl(own)}>Use this repository</button>
              </div>
            </form>
          )}
          <div className="rq-none">
            <button type="button" className="btn" disabled={busy} onClick={() => answer({ action: 'NO_REPO' })}>
              {q.kind === 'NO_CANDIDATE' ? 'I have no repository: generate the code' : 'None of these: generate the code'}
            </button>
          </div>
          {error && <Banner tone="err" icon="alert">{error}</Banner>}
        </>
      )}
    </section>
  )
}

/** The card body: loading, error, forbidden, empty and the waiting questions. */
export function RepoQuestionCard({ workflowId, quiet }: { workflowId?: string; quiet?: boolean }) {
  const { data, error, isLoading, refetch } = useRepoQuestions(workflowId)
  if (!workflowId) return quiet ? null : <Empty icon="check" title="No question yet">A question appears here when a build needs you to choose a repository.</Empty>
  if (error) {
    if (quiet) return null
    return isForbidden(error) ? <Forbidden /> : (
      <ErrorState title="Could not load the repository question" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>
        {(error as { problem?: { detail?: string } }).problem?.detail ?? (error as { message?: string }).message}
      </ErrorState>
    )
  }
  if (isLoading || !data) return quiet ? null : <Skeleton rows={3} label="Loading the repository question" />
  const waiting = data.questions ?? []
  if (waiting.length === 0) {
    return quiet ? null : <Empty icon="check" title="Nothing to decide">No build is waiting for you to choose a repository.</Empty>
  }
  return (
    <div className="rq" aria-label="Repository questions">
      {waiting.map(item => <Question key={`${item.question.sub_skill_id}-${item.build_no}`} item={item} workflowId={workflowId} onDone={() => void window.setTimeout(() => void refetch(), 3000)} />)}
    </div>
  )
}
