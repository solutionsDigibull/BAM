import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { CertEvidence } from './CertEvidence'

const P = 'p-1'
const W = 'w-1'
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': status >= 400 ? 'application/problem+json' : 'application/json' } })
const sub = (id: string, gates: Record<string, string>) => ({ sub_skill_id: id, skill_id: `skill-${id}`, skill_name: `Skill ${id}`, state: 'BUILDING', stage: 'GATES', gates })
const PASS = { IMPORT_RESOLUTION: 'PASSED', REPO_LICENCE: 'PASSED', DEPENDENCY_VULN: 'PASSED', MCP_COMPLIANCE: 'PASSED' }

// The component reads through the proxy with fetch; each test gives one answer per URL.
function serve(routes: Record<string, () => Response>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const hit = Object.entries(routes).find(([k]) => String(url).includes(k))
    return hit ? hit[1]() : json({ title: 'Not found', status: 404 }, 404)
  }))
}
const view = () => render(<QueryClientProvider client={new QueryClient()}><CertEvidence projectId={P} workflowId={W} /></QueryClientProvider>)

beforeEach(() => { vi.useRealTimers() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Certification evidence panel (#842)', () => {
  test('test_842_loading_shows_a_skeleton_first', () => {
    serve({ '/builds': () => json({ workflow_id: W, workflow_state: 'BUILDING', active: false, sub_skills: [] }) })
    view()
    expect(screen.getByText(/Loading the certification evidence/i)).toBeTruthy()
  })

  test('test_842_all_four_results_pass_and_certification_can_be_recorded', async () => {
    serve({ '/builds': () => json({ workflow_id: W, workflow_state: 'CERTIFYING', active: false, sub_skills: [sub('a', PASS), sub('b', PASS)] }) })
    view()
    expect(await screen.findByText('Import gate')).toBeTruthy()
    for (const label of ['Import gate', 'Repository licence', 'Dependency vulnerabilities', 'MCP compliance']) expect(screen.getByText(label)).toBeTruthy()
    expect(screen.getAllByText('Pass')).toHaveLength(4)
    expect(screen.getByText('Ready')).toBeTruthy()
    expect(screen.getByText(/Certification can be recorded/)).toBeTruthy()
  })

  test('test_842_a_missing_result_is_shown_as_blocking_certification', async () => {
    serve({ '/builds': () => json({ workflow_id: W, workflow_state: 'BUILDING', active: true, sub_skills: [sub('a', { IMPORT_RESOLUTION: 'PASSED' })] }) })
    view()
    const licence = (await screen.findByText('Repository licence')).closest('li')!
    expect(within(licence).getByText('Missing')).toBeTruthy()
    expect(within(licence).getByText(/Certification is blocked until it passes/)).toBeTruthy()
    expect(screen.getByText('Blocks certification')).toBeTruthy()
  })

  test('test_842_a_failed_result_shows_the_sub_skill_and_the_reason_from_its_gates', async () => {
    serve({
      '/sub-skills/b/gates': () => json({ sub_skill_id: 'b', attempt: 0, gates: [{ gate: 'REPO_LICENCE', status: 'FAILED', findings: [{ gate: 'REPO_LICENCE', code: 'LICENCE_REFUSED', message: 'GPL-3.0 is not on the allowlist' }] }] }),
      '/builds': () => json({ workflow_id: W, workflow_state: 'BUILDING', active: false, sub_skills: [sub('a', PASS), sub('b', { ...PASS, REPO_LICENCE: 'FAILED' })] }),
    })
    view()
    const licence = (await screen.findByText('Repository licence')).closest('li')!
    expect(within(licence).getByText('Fail')).toBeTruthy()
    expect(within(licence).getByText('Skill b')).toBeTruthy()
    await waitFor(() => expect(within(licence).getByText('GPL-3.0 is not on the allowlist')).toBeTruthy())
  })

  test('test_842_no_sub_skill_built_yet_is_an_empty_state_not_an_error', async () => {
    serve({ '/builds': () => json({ workflow_id: W, workflow_state: 'BUILDING', active: false, sub_skills: [] }) })
    view()
    expect(await screen.findByText('No evidence yet')).toBeTruthy()
    expect(screen.queryByText('Import gate')).toBeNull()
  })

  test('test_842_another_tenant_or_an_unassigned_fde_sees_the_forbidden_state', async () => {
    serve({ '/builds': () => json({ title: 'Not found', status: 404, detail: 'Not found' }, 404) })
    view()
    expect(await screen.findByText('Not available')).toBeTruthy()
    expect(screen.queryByText('Import gate')).toBeNull()
  })

  test('test_842_a_server_error_shows_the_servers_detail_and_a_retry', async () => {
    serve({ '/builds': () => json({ title: 'Service Unavailable', status: 503, detail: 'Storage is unavailable; retry later' }, 503) })
    view()
    expect(await screen.findByText(/The evidence could not be loaded/)).toBeTruthy()
    expect(screen.getByText(/Storage is unavailable; retry later/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })

  test('test_842_the_panel_never_offers_a_write', async () => {
    serve({ '/builds': () => json({ workflow_id: W, workflow_state: 'BUILDING', active: false, sub_skills: [sub('a', PASS)] }) })
    view()
    await screen.findByText('Import gate')
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
    expect(calls.every(c => !(c[1] as RequestInit | undefined)?.method || (c[1] as RequestInit).method === 'GET')).toBe(true)
  })
})
