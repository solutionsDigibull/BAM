import { useState, type FormEvent } from 'react'
import type { Connection } from '@/contracts'
import { api } from '../api/adapter'
import { ifMatchOf, OUTCOME_TEXT, STATUS_LABEL, statusTone, tailText, testReason, useConnections } from '../lib/connections'
import { isForbidden, problemOf } from '../lib/workflowCommit'
import { useAction } from '../lib/data'
import { Banner, Confirm, Empty, ErrorState, Icon, Skeleton, Time } from './ui'
import './connectionsPanel.css'

// The project's secrets (#764, #766): what each workflow needs, whether it is set and tested, and the controls to set or
// replace a value, name where to test it and test it. A value is typed into a password field, sent once and forgotten:
// nothing here can show one again (the server returns only the last characters). Commit is blocked until every required
// secret is set and its test passed; a replaced value is untested again, so it is tested before Commit goes on.

function Row({ c, readOnly }: { c: Connection; readOnly: boolean }) {
  const { busy, run } = useAction()
  const [value, setValue] = useState('')
  const [url, setUrl] = useState(c.test_config?.url ?? '')
  const [message, setMessage] = useState<{ tone: 'ok' | 'err'; text: string } | undefined>()
  const [askNone, setAskNone] = useState(false)
  const isNone = c.test_mode === 'NONE'
  const stored = c.status !== 'MISSING'
  const fail = (e: unknown) => setMessage({ tone: 'err', text: problemOf(e) })
  const act = (label: string, fn: () => Promise<unknown>, done?: string) =>
    run(label, async () => {
      setMessage(undefined)
      try { await fn(); if (done) setMessage({ tone: 'ok', text: done }) } catch (e) { fail(e) }
    })

  const save = (e: FormEvent) => {
    e.preventDefault()
    if (!value.trim()) return
    const typed = value
    setValue('') // forgotten at once, win or lose: it is never kept in the page
    void act(`save ${c.name}`, () => api.setConnectionValue(c.name, typed, ifMatchOf(c)), stored ? 'Value replaced. Test it again before you commit.' : 'Value saved.')
  }
  const saveTarget = (e: FormEvent) => {
    e.preventDefault()
    void act(`target ${c.name}`, () => api.updateConnection(c.name, { test_mode: 'HTTP', test_config: { url: url.trim(), method: 'GET', auth: 'BEARER' } }, ifMatchOf(c)), 'Test address saved. Test the value again.')
  }
  const test = () => run(`test ${c.name}`, async () => {
    setMessage(undefined)
    try {
      const r = await api.testConnection(c.name)
      setMessage({ tone: r.outcome === 'OK' ? 'ok' : 'err', text: r.message || OUTCOME_TEXT[r.outcome] })
    } catch (e) { fail(e) }
  })
  const reason = testReason(c)
  const working = (kind: string) => busy === `${kind} ${c.name}`

  return (
    <li className="cx-row" aria-label={`Secret ${c.name}`}>
      <div className="cx-head">
        <code className="cx-name">{c.name}</code>
        <span className={`pill tone-${statusTone(c.status)}`}>{STATUS_LABEL[c.status]}</span>
        <span className={`pill tone-${c.required ? 'warn' : 'muted'}`}>{c.required ? 'Required' : 'Optional'}</span>
        {stored && <span className="muted xs">{tailText(c)}</span>}
      </div>
      {c.purpose && <p className="muted xs cx-purpose">{c.purpose}</p>}
      {c.last_test_at && c.last_test_outcome && (
        <p className="xs muted">Last test <Time iso={c.last_test_at} />: {OUTCOME_TEXT[c.last_test_outcome]}</p>
      )}
      {!readOnly && (
        <div className="cx-forms">
          <form className="cx-form" onSubmit={save} aria-label={`Set ${c.name}`}>
            <label className="xs muted" htmlFor={`cx-v-${c.name}`}>{stored ? 'Replace value' : 'Value'}</label>
            <input id={`cx-v-${c.name}`} className="cx-input" type="password" autoComplete="off" spellCheck={false}
              value={value} onChange={e => setValue(e.target.value)} placeholder={stored ? 'Enter a new value' : 'Paste the value'} />
            <button className="btn btn-secondary sm" type="submit" disabled={!value.trim() || !!busy}>{working('save') ? 'Saving' : stored ? 'Replace' : 'Save'}</button>
            {stored && <button className="btn btn-ghost sm" type="button" disabled={!!busy}
              onClick={() => void act(`clear ${c.name}`, () => api.clearConnectionValue(c.name, ifMatchOf(c)), 'Value cleared.')}>Clear</button>}
          </form>
          {!isNone && (
            <form className="cx-form" onSubmit={saveTarget} aria-label={`Test address of ${c.name}`}>
              <label className="xs muted" htmlFor={`cx-u-${c.name}`}>Test address (https)</label>
              <input id={`cx-u-${c.name}`} className="cx-input wide" type="url" inputMode="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://service.example.com/v1/ping" />
              <button className="btn btn-secondary sm" type="submit" disabled={!/^https:\/\/\S+$/.test(url.trim()) || !!busy}>Save address</button>
            </form>
          )}
          <div className="cx-actions">
            <button className="btn btn-primary sm" type="button" aria-disabled={!!reason || !!busy || undefined}
              onClick={() => { if (!reason && !busy) void test() }}>{working('test') ? 'Testing' : 'Test connection'}</button>
            {reason && <span className="xs muted" role="note"><Icon name="lock" size={12} /> {reason}</span>}
            {!isNone && stored && (
              <button className="btn btn-ghost sm" type="button" disabled={!!busy} onClick={() => setAskNone(true)}>No remote service</button>
            )}
          </div>
        </div>
      )}
      {message && <p className={`cx-msg tone-${message.tone}`} role="status">{message.text}</p>}
      <Confirm open={askNone} title="This secret has no service to test?" confirmLabel="Yes, nothing to test" onClose={() => setAskNone(false)}
        onConfirm={() => { setAskNone(false); void act(`mode ${c.name}`, () => api.updateConnection(c.name, { test_mode: 'NONE' }, ifMatchOf(c)), 'Marked as having no remote service.') }}
        consequences={[
          'It counts as tested because you say so, not because a service accepted it.',
          'Your statement is recorded in the audit trail with your name.',
          'Use it for a signing key or another value that nothing can check.',
        ]} />
    </li>
  )
}

/** The panel body: loading, error, forbidden, empty and the secrets. `readOnly` shows names and status only. */
export function ConnectionsPanel({ readOnly = false }: { readOnly?: boolean }) {
  const { data, error, isLoading, refetch } = useConnections()
  if (error) {
    if (isForbidden(error)) return <ErrorState title="You can't see this project's secrets">Your account is not assigned to this project.</ErrorState>
    return <ErrorState title="Could not load the secrets" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>{problemOf(error)}</ErrorState>
  }
  if (isLoading || !data) return <Skeleton rows={3} label="Loading the secrets" />
  const rows = data.connections ?? []
  if (rows.length === 0) {
    return <Empty icon="lock" title="No secrets are needed">None of the skills names a secret. Nothing blocks Commit here.</Empty>
  }
  return (
    <div className="cx" aria-label="Secrets">
      <Banner tone="info" icon="lock">Values are never shown again after you save them. Replacing a value makes it untested, and Commit waits for the test.</Banner>
      <ul className="cx-list">{rows.map(c => <Row key={c.name} c={c} readOnly={readOnly} />)}</ul>
    </div>
  )
}
