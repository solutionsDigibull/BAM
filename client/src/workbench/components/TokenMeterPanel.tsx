import { useState, type FormEvent } from 'react'
import type { BudgetState } from '@/contracts'
import {
  canRaise, formatTokens, isForbidden, isUnused, LEVEL_LABEL, levelTone, percent, problemText, raiseCap, SCOPE_LABEL, suggestedCap, useTokenBudget, validNewCap,
} from '../lib/tokenBudget'
import { Banner, ErrorState, Icon, Skeleton } from './ui'
import './tokenMeter.css'

// Translate, the token meter (#776, ADR 0028): tokens used against the cap for each scope (one Sub-Skill build, the
// Workflow build, the knowledge build), with the alert level. When a scope stopped at its cap, the FDE can raise it once
// for that build, then press Retry: the build resumes from its saved step. A second raise is refused by the server.
// An Admin changes the caps of the project; this panel only reads them and makes the one raise.

function Raise({ s, onDone }: { s: BudgetState; onDone: (cap: number) => void }) {
  const [value, setValue] = useState(String(suggestedCap(s.cap)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const id = `tm-raise-${s.scope}`
  const valid = validNewCap(value, s.cap)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!s.build_id || !valid) return
    setBusy(true)
    setError(undefined)
    try {
      const done = await raiseCap(s.scope, s.build_id, Number(value))
      onDone(done.cap)
    } catch (err) {
      setError(problemText(err))
      setBusy(false)
    }
  }
  return (
    <form className="tm-raise" onSubmit={submit} aria-label={`Raise the ${SCOPE_LABEL[s.scope]} limit`}>
      <label htmlFor={id} className="xs muted">New limit (tokens), once for this build</label>
      <div className="tm-raise-row">
        <input id={id} type="number" inputMode="numeric" min={s.cap + 1} step={1} value={value} disabled={busy}
          onChange={e => setValue(e.target.value)} aria-invalid={!valid} />
        <button type="submit" className="btn btn-primary sm" disabled={busy || !valid}>Raise the limit</button>
      </div>
      {!valid && <span className="xs muted" role="status">Use a whole number above {formatTokens(s.cap)}.</span>}
      {error && <span className="xs tm-error" role="alert">{error}</span>}
    </form>
  )
}

function Row({ s, onRaised }: { s: BudgetState; onRaised: (cap: number) => void }) {
  const tone = levelTone(s.level)
  const pct = percent(s)
  return (
    <li className="tm-row" data-scope={s.scope} data-level={s.level}>
      <div className="tm-head">
        <strong>{SCOPE_LABEL[s.scope]}</strong>
        <span className={`pill tone-${tone}`}>{LEVEL_LABEL[s.level]}</span>
        <span className="mono xs">{formatTokens(s.used)} of {formatTokens(s.cap)} tokens</span>
      </div>
      <div className="tm-bar" role="meter" aria-label={`${SCOPE_LABEL[s.scope]} tokens used`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
        aria-valuetext={`${pct}% used, ${LEVEL_LABEL[s.level]}`}>
        <span style={{ width: `${pct}%` }} />
      </div>
      {s.level === 'STOPPED' && s.raised_once && (
        <p className="xs muted tm-note"><Icon name="alert" size={12} /> The limit of this build was raised once already and cannot be raised again. Ask an Admin to change the limits of the project.</p>
      )}
      {canRaise(s) && <Raise s={s} onDone={onRaised} />}
    </li>
  )
}

/** The panel body: loading, error, forbidden, nothing counted yet, and the meter. */
export function TokenMeterPanel() {
  const { data, error, isLoading, refetch } = useTokenBudget()
  const [raisedTo, setRaisedTo] = useState<number>()

  if (error) {
    if (isForbidden(error)) return <ErrorState title="You can't see this project's token budget">Your account is not assigned to this project.</ErrorState>
    return (
      <ErrorState title="Could not load the token budget" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>
        {problemText(error)}
      </ErrorState>
    )
  }
  if (isLoading || !data) return <Skeleton rows={3} label="Loading the token budget" />

  return (
    <section className="tm" aria-label="Token budget">
      <header className="tm-title">
        <h4>Token budget</h4>
        <span className="muted xs">Alerts at {data.first_alert_pct}% and {data.second_alert_pct}%; a build stops at 100%.</span>
      </header>
      {raisedTo !== undefined && (
        <Banner tone="ok" icon="check">
          The limit is now {formatTokens(raisedTo)} tokens. Press Retry on the stopped Sub-Skill and it continues from its saved step.
        </Banner>
      )}
      {isUnused(data) && <p className="muted sm" role="status">No model calls are counted yet. Usage appears here as the builds run.</p>}
      <ul className="tm-list" aria-label="Token usage by scope">
        {data.scopes.map(s => <Row key={s.scope} s={s} onRaised={cap => { setRaisedTo(cap); void refetch() }} />)}
      </ul>
    </section>
  )
}
