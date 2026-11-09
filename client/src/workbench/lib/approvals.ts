// FDE approvals for steps the backend has no record of (Template, Sandbox, Check; 18 Oct 2026, human-gated Workbench
// and Validation). Each is tied to what it approved, so a change makes it lapse by itself: Template to the run and the
// manifest it reviewed, Sandbox to the artifact hash, Check to the test run.
// ponytail: localStorage until the backend records these; move them behind BamApi when it does.
import { useSyncExternalStore } from 'react'
import type { Workflow } from '../api/types'

export interface StepApprovals { template?: string; sandbox?: string; check?: string }

const KEY = 'bam-step-approvals'
let state: StepApprovals = (() => { try { return JSON.parse(localStorage.getItem(KEY) ?? '{}') as StepApprovals } catch { return {} } })()
const subs = new Set<() => void>()
export function approve(patch: StepApprovals) {
  state = { ...state, ...patch }
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch { /* storage blocked */ }
  subs.forEach(f => f())
}
export const getApprovals = () => state
export const useApprovals = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => state)

/** What Template's Approve vouches for: this run's manifest version and names (a code save keeps it; a manifest edit or a new run does not). */
export const templateKey = (wf?: Workflow) => wf?.manifest ? `${wf.runId}:${wf.manifest.version}:${wf.manifest.skills.map(s => s.name).join(',')}` : undefined
export const templateApproved = (a: StepApprovals, wf?: Workflow) => !!templateKey(wf) && a.template === templateKey(wf)
