// The repositories a workflow starts from, read only (#843): the patches applied on top, the fetcher's scan report
// (#755: licence, vulnerabilities, Bandit and pattern findings, malware, health) and the flat component list, from
// the SBAM API. The server finds the report by repository URL and pinned commit; nothing here writes or invents data.
import { useCustom } from '@refinedev/core'
import type {
  DependencyLicence, RepoScanReport, SbomComponent, StaticFinding, VulnFinding, WorkflowRepositories, WorkflowRepository,
} from '@/contracts'

export { isForbidden } from './workflowCode'

type Level = NonNullable<VulnFinding['severity']>
export type Tone = 'ok' | 'warn' | 'err' | 'muted'

/** Highest first, so the findings that block certification are the first ones shown. */
const RANK: Record<Level, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, UNKNOWN: 4 }
export const SEVERITIES: readonly Level[] = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']

const rank = (s?: Level | null): number => RANK[s ?? 'UNKNOWN']

/** HIGH and CRITICAL fail the certification gate; the others are only reported (ADR 0028, #761). */
export function severityTone(s?: Level | null): Tone {
  if (s === 'CRITICAL' || s === 'HIGH') return 'err'
  if (s === 'MEDIUM') return 'warn'
  return 'muted'
}

export const sortBySeverity = <T extends { severity?: Level | null }>(items: readonly T[]): T[] =>
  [...items].sort((a, b) => rank(a.severity) - rank(b.severity))

/** How many findings of each severity, zero ones left out. */
export function severityCounts(items: readonly { severity?: Level | null }[]): { severity: Level; count: number }[] {
  const counts = new Map<Level, number>()
  for (const i of items) counts.set(i.severity ?? 'UNKNOWN', (counts.get(i.severity ?? 'UNKNOWN') ?? 0) + 1)
  return SEVERITIES.filter(s => counts.has(s)).map(s => ({ severity: s, count: counts.get(s)! }))
}

export function verdictTone(verdict?: string | null): Tone {
  if (verdict === 'ALLOWED' || verdict === 'VENDORED') return 'ok'
  if (verdict === 'REFUSED') return 'err'
  return 'warn'
}

export const VERDICT_LABEL: Record<string, string> = { ALLOWED: 'Allowed', REFUSED: 'Refused', UNKNOWN: 'Unknown', VENDORED: 'Vendored' }

export const REFUSAL_TEXT: Record<string, string> = {
  HOST_NOT_APPROVED: 'The host is not an approved source',
  CLONE_FAILED: 'The clone failed',
  LICENCE: 'The licence is not on the allowlist',
  MALWARE: 'Malware was found',
  UNSAFE_TREE: 'The tree has a symlink, a path outside it or a special file',
  TOO_LARGE: 'The repository is too large',
}

export const refusalText = (reason: string): string => REFUSAL_TEXT[reason] ?? reason

/** `https://github.com/acme/lib` -> `acme/lib`. */
export const repoName = (url: string): string => url.replace(/\/+$/, '').split('/').slice(-2).join('/')

export const shortCommit = (commit: string): string => commit.slice(0, 12)

/** The date part of an ISO time; a missing one is said so. */
export const dateOnly = (iso?: string | null): string => (iso ? iso.slice(0, 10) : 'not reported')

export function healthLine(h: RepoScanReport['health'] = {}): string[] {
  const out = [`Last commit ${dateOnly(h.last_commit_at)}`, `last release ${dateOnly(h.last_release_at)}`]
  if (h.archived === true) out.push('archived')
  else if (h.archived === false) out.push('not archived')
  return out
}

/** `path:line`, or just the path when the finding has no line. */
export const where = (f: Pick<StaticFinding, 'path' | 'line'>): string => (f.line ? `${f.path}:${f.line}` : f.path)

/** Dependencies whose licence blocks certification (copyleft or off the allowlist), first. */
export const refusedDependencies = (deps: readonly DependencyLicence[]): DependencyLicence[] => deps.filter(d => d.verdict === 'REFUSED')

/** Does anything in the report block certification (a refusal, a refused licence, HIGH or CRITICAL, malware)? */
export function reportBlocks(r: RepoScanReport): boolean {
  return (
    r.decision === 'REFUSED' ||
    r.licence?.verdict === 'REFUSED' ||
    refusedDependencies(r.dependency_licences ?? []).length > 0 ||
    (r.vulnerabilities ?? []).some(v => severityTone(v.severity) === 'err') ||
    (r.clam_findings ?? []).length > 0
  )
}

export type RepoTotals = { repositories: number; withReport: number; blocking: number; patches: number; components: number }

export function totals(repos: readonly WorkflowRepository[]): RepoTotals {
  return {
    repositories: repos.length,
    withReport: repos.filter(r => !!r.report).length,
    blocking: repos.filter(r => !r.report || reportBlocks(r.report)).length,
    patches: repos.reduce((n, r) => n + r.patches.length, 0),
    components: repos.reduce((n, r) => n + r.sbom.length, 0),
  }
}

export const componentLabel = (c: SbomComponent): string => (c.kind === 'repository' ? 'Repository' : (c.ecosystem ?? 'Dependency'))

export function useWorkflowRepos(workflowId: string | undefined) {
  const { query } = useCustom<WorkflowRepositories>({
    url: 'workflow-repos', method: 'get', config: { query: { workflowId } },
    queryOptions: { enabled: !!workflowId, retry: false }, // the screen has its own Retry
  })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
