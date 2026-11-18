import type { ReactNode } from 'react'
import type { RepoScanReport, WorkflowRepository } from '@/contracts'
import {
  componentLabel, healthLine, isForbidden, refusalText, repoName, reportBlocks, severityCounts, severityTone, shortCommit,
  sortBySeverity, totals, useWorkflowRepos, VERDICT_LABEL, verdictTone, where, type Tone,
} from '../lib/workflowRepos'
import { Banner, Copy, Empty, ErrorState, Skeleton } from './ui'
import './repoPanel.css'

// Translate (4.3), the repositories the workflow starts from, read only (#843): the pinned commit, the licence verdict,
// the vulnerability, Bandit, pattern and malware findings and the health record of the fetcher's scan report (#755),
// the patches applied on top with their reasons (#757) and the components (the repository and its dependencies).
// A repository whose report is not stored is listed with a notice, never as an error. Nothing here can change anything.

const SHOWN = 10

const Pill = ({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) =>
  <span className={`pill tone-${tone}`} title={title}>{children}</span>

function Forbidden() {
  return <ErrorState title="You can't see this workflow's repositories">Your account is not assigned to this project, or the workflow does not exist.</ErrorState>
}

function More({ count }: { count: number }) {
  return count > SHOWN ? <li className="rp-more-row muted xs">and {count - SHOWN} more</li> : null
}

function Findings({ report }: { report: RepoScanReport }) {
  const vulns = sortBySeverity(report.vulnerabilities ?? [])
  const statics = sortBySeverity(report.static_findings ?? [])
  const clam = report.clam_findings ?? []
  const counts = severityCounts(vulns)
  return (
    <div className="rp-block">
      <h5>Vulnerabilities</h5>
      {vulns.length === 0 ? <p className="rp-none">None known for the dependencies that were read.</p> : (
        <>
          <p className="rp-counts">{counts.map(c => <Pill key={c.severity} tone={severityTone(c.severity)}>{c.severity} · {c.count}</Pill>)}</p>
          <ul className="rp-list" aria-label="Vulnerabilities">
            {vulns.slice(0, SHOWN).map((v, i) => (
              <li key={`${v.id}-${i}`}>
                <Pill tone={severityTone(v.severity)}>{v.severity ?? 'UNKNOWN'}</Pill>
                <code>{v.id}</code> <span>{v.package}@{v.version}</span>
                {v.summary && <span className="muted xs"> · {v.summary}</span>}
              </li>
            ))}
            <More count={vulns.length} />
          </ul>
        </>
      )}
      <h5>Bandit and pattern findings</h5>
      {statics.length === 0 ? <p className="rp-none">No finding.</p> : (
        <ul className="rp-list" aria-label="Bandit and pattern findings">
          {statics.slice(0, SHOWN).map((f, i) => (
            <li key={i}>
              <Pill tone={severityTone(f.severity)}>{f.severity ?? 'UNKNOWN'}</Pill>
              <code>{f.tool}:{f.rule}</code> <span className="rp-path">{where(f)}</span>
              {f.message && <span className="muted xs"> · {f.message}</span>}
            </li>
          ))}
          <More count={statics.length} />
        </ul>
      )}
      <h5>Malware scan</h5>
      {clam.length === 0 ? <p className="rp-none">Clean: {report.files_scanned} files scanned, nothing quarantined.</p> : (
        <ul className="rp-list" aria-label="Quarantined files">
          {clam.slice(0, SHOWN).map((c, i) => <li key={i}><Pill tone="err">Quarantined</Pill><span className="rp-path">{c.path}</span> <code>{c.signature}</code></li>)}
          <More count={clam.length} />
        </ul>
      )}
    </div>
  )
}

function ReportBlock({ report }: { report: RepoScanReport }) {
  const licence = report.licence
  const deps = report.dependency_licences ?? []
  const refused = deps.filter(d => d.verdict === 'REFUSED')
  const health = report.health ?? {}
  return (
    <>
      <div className="rp-block">
        <h5>Decision and licence</h5>
        <p className="rp-line">
          <Pill tone={verdictTone(report.decision)}>{VERDICT_LABEL[report.decision] ?? report.decision}</Pill>
          {licence ? (
            <>
              <Pill tone={verdictTone(licence.verdict)}>{licence.spdx ?? 'No licence found'} · {VERDICT_LABEL[licence.verdict] ?? licence.verdict}</Pill>
              <span className="muted xs">read from {licence.source}</span>
            </>
          ) : <span className="muted xs">No licence result</span>}
        </p>
        {licence?.override && <p className="rp-line xs">A person accepted this licence: <em>{licence.override.reason}</em> ({licence.override.by})</p>}
        {(report.refusals ?? []).length > 0 && (
          <ul className="rp-list" aria-label="Why it was refused">{(report.refusals ?? []).map(r => <li key={r}><Pill tone="err">Refused</Pill>{refusalText(r)}</li>)}</ul>
        )}
        {refused.length > 0 && (
          <ul className="rp-list" aria-label="Dependencies with a refused licence">
            {refused.slice(0, SHOWN).map((d, i) => <li key={i}><Pill tone="err">Refused</Pill>{d.name}@{d.version} <span className="muted xs">{d.spdx ?? 'no licence found'} ({d.ecosystem})</span></li>)}
            <More count={refused.length} />
          </ul>
        )}
        <p className="rp-line xs muted">{healthLine(health).join(' · ')}{(health.flags ?? []).length > 0 && ` · ${(health.flags ?? []).join(', ')}`}</p>
      </div>
      <Findings report={report} />
      {(report.unsafe_paths ?? []).length > 0 && (
        <div className="rp-block">
          <h5>Unsafe paths refused</h5>
          <ul className="rp-list">{(report.unsafe_paths ?? []).slice(0, SHOWN).map(p => <li key={p}><span className="rp-path">{p}</span></li>)}<More count={(report.unsafe_paths ?? []).length} /></ul>
        </div>
      )}
      {(report.notes ?? []).length > 0 && <ul className="rp-list muted xs" aria-label="Notes">{(report.notes ?? []).map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </>
  )
}

function RepoCard({ repo }: { repo: WorkflowRepository }) {
  const report = repo.report ?? undefined
  const attention = !report || reportBlocks(report)
  return (
    <section className="rp-card" aria-label={repoName(repo.url)} data-repo={repo.url}>
      <header className="rp-head">
        <h4>{repoName(repo.url)}</h4>
        <Copy value={repo.commit} display={shortCommit(repo.commit)} label="commit" />
        {repo.licence && <Pill tone={verdictTone(report?.licence?.verdict ?? 'UNKNOWN')} title="The licence the manifest recorded">{repo.licence}</Pill>}
        <Pill tone={attention ? 'warn' : 'ok'}>{!report ? 'No scan report' : attention ? 'Needs attention' : 'Clean'}</Pill>
      </header>
      <p className="rp-line xs muted">Used by {repo.sub_skills.join(', ') || 'no Sub-Skill'} · stored as <code>{repo.repo_dir}</code></p>
      {!report
        ? <Banner tone="warn" icon="alert">No scan report is attached for this repository at this commit. Certification needs one: without it the licence and vulnerability results fail closed.</Banner>
        : <ReportBlock report={report} />}
      <div className="rp-block">
        <h5>Patches ({repo.patches.length})</h5>
        {repo.patches.length === 0 ? <p className="rp-none">Used as is: no patch was applied.</p> : (
          <ol className="rp-list" aria-label="Patches">
            {repo.patches.map(p => (
              <li key={p.path}><code className="rp-path">{p.path.slice(p.path.lastIndexOf('/') + 1)}</code> <span>{p.reason}</span>
                <span className="muted xs"> · changes {p.files.join(', ')}</span></li>
            ))}
          </ol>
        )}
      </div>
      <details className="rp-block rp-sbom">
        <summary>Components ({repo.sbom.length})</summary>
        <ul className="rp-list" aria-label="Components">
          {repo.sbom.map((c, i) => (
            <li key={`${c.name}-${i}`}>
              <span className="rp-kind">{componentLabel(c)}</span> <strong>{c.name}</strong> <span>{c.version.length === 40 ? shortCommit(c.version) : c.version}</span>
              <span className="muted xs"> · {c.licence ?? 'licence not known'}</span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  )
}

/** The panel body: loading, error, forbidden, empty and the repositories. */
export function RepositoryPanel({ workflowId }: { workflowId?: string }) {
  const { data, error, isLoading, refetch } = useWorkflowRepos(workflowId)
  if (!workflowId) return <Empty icon="file" title="No repository yet">The repositories appear once the build has started.</Empty>
  if (error) {
    return isForbidden(error) ? <Forbidden /> : (
      <ErrorState title="Could not load the repositories" action={<button className="btn btn-primary" onClick={() => refetch()}>Retry</button>}>
        {(error as { problem?: { detail?: string } }).problem?.detail ?? (error as { message?: string }).message}
      </ErrorState>
    )
  }
  if (isLoading || !data) return <Skeleton rows={6} label="Loading the repositories" />
  if (data.repositories.length === 0) {
    return <Empty icon="file" title="No repository">No repository was used, or no scan report is attached. The code of this workflow was generated by SBAM.</Empty>
  }
  const t = totals(data.repositories)
  return (
    <div className="rp" aria-label="Repositories">
      <p className="rp-summary" role="status">
        <strong>{t.repositories}</strong> {t.repositories === 1 ? 'repository' : 'repositories'} · <strong>{t.withReport}</strong> with a scan report ·{' '}
        <strong>{t.blocking}</strong> need attention · <strong>{t.patches}</strong> {t.patches === 1 ? 'patch' : 'patches'} · <strong>{t.components}</strong> components
      </p>
      {data.repositories.map(r => <RepoCard key={`${r.url}@${r.commit}`} repo={r} />)}
      <p className="rp-foot muted xs">Read only. Each report shown is the one stored for the URL and pinned commit of its repository.</p>
    </div>
  )
}
