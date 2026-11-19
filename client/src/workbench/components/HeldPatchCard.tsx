import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { HeldPatch } from '@/contracts'
import {
  decideHeldPatch, type Decision, ifMatchOf, isForbidden, MAX_DECISION_REASON, newDecisionKey, OP_LABEL, pathLabel, problemText, reasonProblem,
  showValue, useHeldPatch,
} from '../lib/heldPatch'
import { gateLabel } from '../lib/workflowBuilds'
import { Banner, Copy, Dialog, ErrorState, Icon, Skeleton } from './ui'
import './heldPatch.css'

// A correction proposed a change to the Sub-Skill's ISATVON that changes what it means (a looser bound, a weaker gate), so
// the build holds the Sub-Skill for the FDE (#715, follows #499). Accept applies the patch through the same validated, audited
// path as an edit and retries from it; Reject keeps the approved ISATVON and retries. The server checks the Sub-Skill's hash
// (If-Match) and refuses a decision that is not allowed; the model's rationale is shown as text, never as markup.

const ACCEPT_HELP = "The patch is applied to the Sub-Skill's ISATVON and the build starts again from it. The change is recorded."
const REJECT_HELP = 'The approved ISATVON stays as it is and the build starts again from it. Your reason is recorded.'

function Decide({ workflowId, held, decision, onClose }: { workflowId: string; held: HeldPatch; decision: Decision | undefined; onClose: (sent?: string) => void }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const key = useRef(newDecisionKey())
  const problem = decision ? reasonProblem(decision, reason) : undefined
  const close = (sent?: string) => { setReason(''); setError(undefined); setBusy(false); key.current = newDecisionKey(); onClose(sent) }
  const send = async () => {
    if (!decision || problem) return
    setBusy(true)
    setError(undefined)
    try {
      const r = await decideHeldPatch(workflowId, held.sub_skill_id, decision, reason.trim() || undefined, key.current, decision === 'accept' ? ifMatchOf(held) : undefined)
      close(`${r.decision === 'ACCEPTED' ? 'Patch accepted' : 'Patch rejected'}: build ${r.build_no} started.`)
    } catch (e) {
      setError(problemText(e))
      setBusy(false)
    }
  }
  const accept = decision === 'accept'
  return (
    <Dialog open={!!decision} title={accept ? 'Accept the ISATVON patch?' : 'Reject the ISATVON patch?'} onClose={() => close()} footer={<>
      <button className="btn btn-secondary" onClick={() => close()}>Cancel</button>
      <button className="btn btn-primary" aria-disabled={!!problem || busy || undefined} onClick={() => void send()}>{busy ? 'Sending...' : accept ? 'Accept' : 'Reject'}</button></>}>
      <p className="muted xs">{accept ? ACCEPT_HELP : REJECT_HELP}</p>
      <label className="xs muted" htmlFor="hp-reason">Reason{accept ? ' (optional)' : ''}</label>
      <textarea id="hp-reason" className="hp-reason" rows={3} maxLength={MAX_DECISION_REASON} value={reason} onChange={e => setReason(e.target.value)} disabled={busy} />
      <span className="muted xs">{reason.length}/{MAX_DECISION_REASON}</span>
      {error && <Banner tone="err" icon="alert">{error}</Banner>}
    </Dialog>
  )
}

const shortHash = (h: string): string => h.replace(/^sha256:/, '').slice(0, 12)

function Patch({ held, onDecide }: { held: HeldPatch; onDecide: (d: Decision) => void }) {
  return (
    <section className="hp-card" aria-label="Held ISATVON patch">
      <header className="hp-head">
        <Icon name="alert" />
        <div>
          <h4>An ISATVON patch waits for your decision</h4>
          <p className="muted xs">Attempt {held.attempt} proposed it after the {gateLabel(held.failed_gate)} gate failed. The Sub-Skill is held until you decide.</p>
        </div>
      </header>
      <p className="hp-why" aria-label="Rationale">{held.rationale}</p>
      {(held.changed_paths ?? []).length > 0 && (
        <p className="xs muted">Changes in: {(held.changed_paths ?? []).map(p => <code key={p} className="hp-chip">{p}</code>)}</p>
      )}
      <ul className="hp-ops" aria-label="Patch operations">
        {(held.ops ?? []).map((o, i) => (
          <li key={i}>
            <span className="pill tone-muted">{OP_LABEL[o.op]}</span>
            <span className="hp-path">{pathLabel(o.path)}</span>
            {o.op !== 'remove' && showValue(o.value) !== '' && <code className="hp-value">{showValue(o.value)}</code>}
          </li>
        ))}
      </ul>
      <dl className="kv inline hp-hash">
        <div><dt>Approved definition</dt><dd><Copy value={held.isatvon_sha256_before} display={shortHash(held.isatvon_sha256_before)} label="approved definition hash" /></dd></div>
        <div><dt>With the patch</dt><dd><Copy value={held.isatvon_sha256_after} display={shortHash(held.isatvon_sha256_after)} label="patched definition hash" /></dd></div>
      </dl>
      <div className="hp-actions">
        <button type="button" className="btn btn-primary sm" onClick={() => onDecide('accept')}><Icon name="check" size={12} />Accept the patch</button>
        <button type="button" className="btn btn-secondary sm" onClick={() => onDecide('reject')}><Icon name="x" size={12} />Reject it</button>
      </div>
    </section>
  )
}

/** The card for one Sub-Skill that waits for the FDE: loading, error, forbidden, nothing waiting, and the patch. */
export function HeldPatchCard({ workflowId, subId, name }: { workflowId: string; subId: string; name: string }) {
  const { held, loaded, error, isLoading, refetch } = useHeldPatch(workflowId, subId)
  const qc = useQueryClient()
  const [deciding, setDeciding] = useState<Decision>()
  const [sent, setSent] = useState<string>()
  if (error) return isForbidden(error) ? <ErrorState title="You can't see this patch">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
    : <ErrorState title={`Could not load the patch for ${name}`} action={<button className="btn btn-primary" onClick={() => void refetch()}>Retry</button>}>{problemText(error)}</ErrorState>
  if (isLoading || !loaded) return <Skeleton rows={2} label={`Loading the patch for ${name}`} />
  if (sent) return <Banner tone="ok" icon="check">{sent}</Banner>
  if (!held) return <p className="muted xs">No ISATVON patch is waiting for {name}. Retry starts the build again.</p>
  return (
    <>
      <Patch held={held} onDecide={setDeciding} />
      <Decide workflowId={workflowId} held={held} decision={deciding} onClose={m => { setDeciding(undefined); if (m) { setSent(m); void qc.invalidateQueries() } }} />
    </>
  )
}
