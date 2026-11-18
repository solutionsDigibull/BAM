// Certification evidence from the build (#842, #857): the four results #761 records on each Sub-Skill's bundle hash and the
// Workflow's UI bundle result (#857, one per Workflow), all of which certification needs. Pure rules over `GET W/builds`; the screen only draws them. A result that is not PASSED for every
// Sub-Skill blocks certification, and a missing one (never reached, skipped) is shown as missing, never as a pass.
import type { BuildRow, GateName, GateStatus } from '@/contracts'

export const EVIDENCE_GATES: readonly { gate: GateName; label: string; detail: string; scope?: 'workflow' }[] = [
  { gate: 'IMPORT_RESOLUTION', label: 'Import gate', detail: 'Every import resolves in the sandbox and no import has a side effect.' },
  { gate: 'REPO_LICENCE', label: 'Repository licence', detail: 'The licence of each repository and its dependencies is on the allowlist, or an override is recorded.' },
  { gate: 'DEPENDENCY_VULN', label: 'Dependency vulnerabilities', detail: 'No known high or critical vulnerability in the dependencies.' },
  { gate: 'MCP_COMPLIANCE', label: 'MCP compliance', detail: 'Each Sub-Skill has an action tool and an independent check tool with valid schemas.' },
  { gate: 'UI_BUNDLE', label: 'UI bundle', detail: "The workflow's UI bundle is present, its hash matches the manifest and the hosts it may reach are valid.", scope: 'workflow' },
]

export type EvidenceState = 'pass' | 'fail' | 'running' | 'missing'
export interface SubRef { subSkillId: string; skillName: string }
export interface EvidenceRow {
  gate: GateName
  label: string
  detail: string
  state: EvidenceState
  total: number
  passed: number
  failing: SubRef[]
  missing: SubRef[]
  /** Certification cannot be recorded while the result is not a pass. */
  blocking: boolean
  /** True for a result of the whole Workflow (the UI bundle), not of each Sub-Skill. */
  workflow: boolean
}

const statusOf = (row: BuildRow, gate: GateName): GateStatus | undefined => row.gates?.[gate]
const ref = (r: BuildRow): SubRef => ({ subSkillId: r.sub_skill_id, skillName: r.skill_name || r.skill_id })

const WORKFLOW_STATE: Partial<Record<GateStatus, EvidenceState>> = { PASSED: 'pass', FAILED: 'fail', RUNNING: 'running' }

export function evidenceRows(rows: readonly BuildRow[], workflowGates?: Partial<Record<GateName, GateStatus>>): EvidenceRow[] {
  return EVIDENCE_GATES.map(({ gate, label, detail, scope }) => {
    if (scope === 'workflow') {
      const status = workflowGates?.[gate]
      const state: EvidenceState = (status && WORKFLOW_STATE[status]) || 'missing'
      return { gate, label, detail, state, total: 1, passed: state === 'pass' ? 1 : 0, failing: [], missing: [], blocking: state !== 'pass', workflow: true }
    }
    const failing = rows.filter(r => statusOf(r, gate) === 'FAILED').map(ref)
    const running = rows.filter(r => statusOf(r, gate) === 'RUNNING')
    const passed = rows.filter(r => statusOf(r, gate) === 'PASSED').length
    const missing = rows.filter(r => { const s = statusOf(r, gate); return s === undefined || s === 'PENDING' || s === 'SKIPPED' }).map(ref)
    const state: EvidenceState = failing.length ? 'fail' : running.length ? 'running' : !rows.length || missing.length ? 'missing' : 'pass'
    return { gate, label, detail, state, total: rows.length, passed, failing, missing, blocking: state !== 'pass', workflow: false }
  })
}

/** True while any of the results stops certification. */
export const certificationBlocked = (rows: readonly EvidenceRow[]): boolean => rows.some(r => r.blocking)

/** The messages a gate's findings give, for the "why" line; at most `max`, each cut so one finding cannot flood the row. */
export function reasonsOf(findings: readonly { gate: string; message: string }[] | undefined, gate: GateName, max = 3): string[] {
  return (findings ?? []).filter(f => f.gate === gate).slice(0, max).map(f => (f.message.length > 240 ? `${f.message.slice(0, 240)}…` : f.message))
}

const base = (p: string, w: string) => `projects/${p}/workflows/${w}`
export const buildsPath = (p: string, w: string) => `${base(p, w)}/builds`
export const gatesPath = (p: string, w: string, subId: string) => `${base(p, w)}/sub-skills/${subId}/gates`

/** The project id of the current page (`/projects/{id}/workbench`), or undefined. */
export const projectIdOfPath = (path: string): string | undefined => /\/projects\/([^/]+)/.exec(path)?.[1]
