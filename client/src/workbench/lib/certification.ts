// Certification checklist for the current manifest, from backend evidence only. Every item is bound to the
// manifest version/hash (and SOW version or run ID where relevant). Evidence for another hash is "stale",
// never a pass: a newer edited artifact cannot be certified by older results.
import type { Snapshot, Tests, VersionEntry } from '../api/types'

export type CertState = 'pass' | 'fail' | 'pending' | 'stale' | 'running' | 'unavailable'
export interface CertItem { key: string; label: string; state: CertState; detail: string; ref?: string }
export interface Certification {
  items: CertItem[]
  /** All items pass for the current manifest hash. */
  certified: boolean
  manifest?: { version: string; hash: string }
}

const short = (h: string) => h.slice(0, 12)

/** Results that belong to the current manifest, or undefined. */
export const currentTests = (snap: Snapshot): Tests | undefined =>
  snap.tests && snap.tests.manifestHash === snap.workflow?.manifest?.hash ? snap.tests : undefined

export function certification(snap: Snapshot): Certification {
  const wf = snap.workflow
  const m = wf?.manifest
  const sow = snap.sow
  const tests = currentTests(snap)
  const oldTests = !tests && snap.tests ? snap.tests : undefined
  const staleRun = (t: Tests) => `Results are for manifest ${t.manifestVersion} (${short(t.manifestHash)}), not the current ${m?.version} (${m ? short(m.hash) : '—'}). Re-run tests.`
  const items: CertItem[] = []

  // 1. SOW approval, tied to the SOW version the manifest was built from.
  if (!sow?.approval) items.push({ key: 'sow', label: 'SOW approval', state: sow?.invalidated && m ? 'stale' : 'pending',
    detail: sow?.invalidated ? `Approval of v${sow.invalidated.version} was invalidated: ${sow.invalidated.reason}` : 'Approve the SOW' })
  else if (m && m.sowVersion !== sow.approval.version) items.push({ key: 'sow', label: 'SOW approval', state: 'stale',
    detail: `Manifest was built from SOW v${m.sowVersion}; the approved SOW is now v${sow.approval.version}. Regenerate the contract in 3.2.` })
  else items.push({ key: 'sow', label: 'SOW approval', state: 'pass', detail: `SOW v${sow.approval.version} approved by ${sow.approval.by}`, ref: `${sow.meta.sowId} v${sow.approval.version}` })

  // 2. AST parse: activity C of the validation run.
  const c = wf?.activities.find(a => a.key === 'C')
  const stale3 = snap.steps[2]?.stale
  if (!wf || !c) items.push({ key: 'ast', label: 'Translation check', state: 'pending', detail: 'Runs as Validation activity C after the contract is generated' })
  else if (c.status === 'running' || c.status === 'queued') items.push({ key: 'ast', label: 'Translation check', state: 'running', detail: 'Activity C is running' })
  else if (c.status === 'failed') items.push({ key: 'ast', label: 'Translation check', state: 'fail', detail: c.reason ?? 'Activity C failed', ref: wf.runId })
  else items.push({ key: 'ast', label: 'Translation check', state: stale3 ? 'stale' : 'pass',
    detail: stale3 ? 'An earlier screen was reopened; re-run validation' : `Activity C passed on attempt ${c.attempts}${m ? ` for ${m.version}` : ''}`, ref: wf.runId })

  // 3. Input/output contract checks: activity A (input contract) + Outcome-schema assertions (output contract).
  const a = wf?.activities.find(x => x.key === 'A')
  const outcome = tests?.assertions.filter(x => x.name === 'Output conforms to Outcome schema') ?? []
  if (!a || a.status === 'queued') items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'pending', detail: 'Input contract: Validation activity A. Output contract: Test results sandbox run' })
  else if (a.status === 'running') items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'running', detail: 'Activity A is validating the contract' })
  else if (a.status === 'failed') items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'fail', detail: a.reason ?? 'Contract validation failed', ref: wf!.runId })
  else if (oldTests) items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'stale', detail: `Input contract passed. ${staleRun(oldTests)}`, ref: oldTests.runId })
  else if (!tests || !outcome.length) items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'pending', detail: 'Input contract passed (activity A). Output contract is asserted in the Test results sandbox run', ref: wf!.runId })
  else if (outcome.some(x => x.status === 'fail')) items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'fail', detail: 'An Outcome-schema assertion failed', ref: tests.runId })
  else items.push({ key: 'contracts', label: 'Input/output contract checks', state: 'pass', detail: `Input contract (A) and ${outcome.length} Outcome-schema assertions passed`, ref: tests.runId })

  // 4. Sandbox and assertions for this exact hash.
  if (oldTests) items.push({ key: 'sandbox', label: 'Sandbox and assertions', state: 'stale', detail: staleRun(oldTests), ref: oldTests.runId })
  else if (!tests) items.push({ key: 'sandbox', label: 'Sandbox and assertions', state: 'pending', detail: 'Run the sandbox and its assertions in Sandbox' })
  else if ([tests.sandbox, tests.promptfoo].some(x => x.status === 'running' || x.status === 'queued')) items.push({ key: 'sandbox', label: 'Sandbox and assertions', state: 'running', detail: 'Test run in progress', ref: tests.runId })
  else if ([tests.sandbox, tests.promptfoo].some(x => x.status === 'failed')) items.push({ key: 'sandbox', label: 'Sandbox and assertions', state: 'fail',
    detail: tests.promptfoo.error ?? tests.sandbox.error ?? 'Test run failed', ref: tests.runId })
  else items.push({ key: 'sandbox', label: 'Sandbox and assertions', state: 'pass',
    detail: `${tests.assertions.filter(x => x.status === 'pass').length} of ${tests.assertions.length} assertions passed`, ref: tests.runId })

  // 5. Reviewer approval: manifest + code approved for this hash, and the clean run reviewed.
  const approved = !!m && wf?.approval?.hash === m.hash
  const reviewed = !!tests?.reviewedAt
  items.push({ key: 'review', label: 'Reviewer approval', state: approved && reviewed ? 'pass' : 'pending',
    detail: approved && reviewed ? `Manifest and code approved by ${wf!.approval!.by}; results reviewed`
      : !approved ? 'Approve the manifest and code (Manifest JSON), then review the clean run (Test results)' : 'Review the clean run in Test results',
    ref: approved ? `approval ${short(wf!.approval!.hash)}` : undefined })

  return { items, certified: !!m && items.every(i => i.state === 'pass'), manifest: m && { version: m.version, hash: m.hash } }
}

/** The latest certified (or frozen) manifest, the baseline for release comparisons. */
export const certifiedBaseline = (snap: Snapshot): VersionEntry | undefined =>
  [...snap.versions].reverse().find(v => v.kind === 'certified' || v.kind === 'frozen')
