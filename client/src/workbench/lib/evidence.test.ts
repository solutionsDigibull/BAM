import { describe, expect, test } from 'vitest'
import type { BuildRow } from '@/contracts'
import { certificationBlocked, evidenceRows, projectIdOfPath, reasonsOf } from './evidence'

const row = (id: string, gates: BuildRow['gates']): BuildRow =>
  ({ sub_skill_id: id, skill_id: `skill-${id}`, skill_name: `Skill ${id}`, state: 'BUILDING', stage: 'GATES', gates }) as unknown as BuildRow
const ALL_PASS = { IMPORT_RESOLUTION: 'PASSED', REPO_LICENCE: 'PASSED', DEPENDENCY_VULN: 'PASSED', MCP_COMPLIANCE: 'PASSED' } as BuildRow['gates']
const state = (rows: BuildRow[], gate: string) => evidenceRows(rows).find(r => r.gate === gate)!

describe('certification evidence rules (#842)', () => {
  test('test_842_the_results_are_listed_in_order', () => {
    expect(evidenceRows([row('a', ALL_PASS)]).map(r => r.gate)).toEqual(['IMPORT_RESOLUTION', 'REPO_LICENCE', 'DEPENDENCY_VULN', 'MCP_COMPLIANCE', 'UI_BUNDLE'])
  })

  test('test_857_the_ui_bundle_is_one_result_of_the_workflow_and_blocks_until_it_passes', () => {
    const ui = (g?: Record<string, string>) => evidenceRows([row('a', ALL_PASS)], g as never).find(r => r.gate === 'UI_BUNDLE')!
    expect(ui()).toMatchObject({ state: 'missing', workflow: true, blocking: true, total: 1 })
    expect(ui({ UI_BUNDLE: 'PENDING' }).state).toBe('missing')
    expect(ui({ UI_BUNDLE: 'RUNNING' }).state).toBe('running')
    expect(ui({ UI_BUNDLE: 'FAILED' })).toMatchObject({ state: 'fail', blocking: true })
    expect(ui({ UI_BUNDLE: 'PASSED' })).toMatchObject({ state: 'pass', passed: 1, blocking: false })
    expect(certificationBlocked(evidenceRows([row('a', ALL_PASS)]))).toBe(true)
    expect(certificationBlocked(evidenceRows([row('a', ALL_PASS)], { UI_BUNDLE: 'PASSED' }))).toBe(false)
  })

  test('test_842_all_passed_for_every_sub_skill_does_not_block_certification', () => {
    const rows = evidenceRows([row('a', ALL_PASS), row('b', ALL_PASS)], { UI_BUNDLE: 'PASSED' })
    expect(rows.filter(r => !r.workflow).every(r => r.state === 'pass' && r.passed === 2 && r.total === 2)).toBe(true)
    expect(certificationBlocked(rows)).toBe(false)
  })

  test('test_842_one_failed_sub_skill_fails_the_result_and_names_it', () => {
    const r = state([row('a', ALL_PASS), row('b', { ...ALL_PASS, REPO_LICENCE: 'FAILED' })], 'REPO_LICENCE')
    expect(r.state).toBe('fail')
    expect(r.failing).toEqual([{ subSkillId: 'b', skillName: 'Skill b' }])
    expect(r.blocking).toBe(true)
  })

  test('test_842_a_result_that_was_never_reached_is_missing_and_blocks_certification', () => {
    const rows = evidenceRows([row('a', { IMPORT_RESOLUTION: 'PASSED' } as BuildRow['gates']), row('b', undefined)])
    expect(rows.find(r => r.gate === 'MCP_COMPLIANCE')!.state).toBe('missing')
    expect(rows.find(r => r.gate === 'MCP_COMPLIANCE')!.missing.map(s => s.subSkillId)).toEqual(['a', 'b'])
    expect(certificationBlocked(rows)).toBe(true)
  })

  test('test_842_pending_and_skipped_count_as_missing_never_as_a_pass', () => {
    expect(state([row('a', { ...ALL_PASS, DEPENDENCY_VULN: 'PENDING' })], 'DEPENDENCY_VULN').state).toBe('missing')
    expect(state([row('a', { ...ALL_PASS, DEPENDENCY_VULN: 'SKIPPED' })], 'DEPENDENCY_VULN').state).toBe('missing')
  })

  test('test_842_a_running_result_is_running_and_still_blocks', () => {
    const r = state([row('a', ALL_PASS), row('b', { ...ALL_PASS, IMPORT_RESOLUTION: 'RUNNING' })], 'IMPORT_RESOLUTION')
    expect(r).toMatchObject({ state: 'running', passed: 1, total: 2, blocking: true })
  })

  test('test_842_a_failure_wins_over_a_missing_result', () => {
    const r = state([row('a', { ...ALL_PASS, MCP_COMPLIANCE: 'FAILED' }), row('b', { IMPORT_RESOLUTION: 'PASSED' } as BuildRow['gates'])], 'MCP_COMPLIANCE')
    expect(r.state).toBe('fail')
  })

  test('test_842_no_sub_skill_built_yet_blocks_every_result', () => {
    const rows = evidenceRows([])
    expect(rows.filter(r => !r.workflow).every(r => r.state === 'missing' && r.total === 0)).toBe(true)
    expect(certificationBlocked(rows)).toBe(true)
  })

  test('test_842_reasons_are_the_gates_own_findings_cut_to_a_few_short_lines', () => {
    const findings = [
      { gate: 'REPO_LICENCE', message: 'GPL-3.0 is not on the allowlist' },
      { gate: 'MCP_COMPLIANCE', message: 'another gate' },
      { gate: 'REPO_LICENCE', message: 'x'.repeat(300) },
    ]
    const out = reasonsOf(findings, 'REPO_LICENCE')
    expect(out[0]).toBe('GPL-3.0 is not on the allowlist')
    expect(out).toHaveLength(2)
    expect(out[1]!.length).toBeLessThanOrEqual(241)
    expect(reasonsOf(undefined, 'REPO_LICENCE')).toEqual([])
  })

  test('test_842_the_project_id_comes_from_the_page_path', () => {
    expect(projectIdOfPath('/projects/p-1/workbench')).toBe('p-1')
    expect(projectIdOfPath('/login-fde')).toBeUndefined()
  })
})
