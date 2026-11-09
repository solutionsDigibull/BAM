// Global destination outside the four workflow stages (BAM v3): Admin > Masters (profile menu). Read-only with
// respect to the workflow: nothing here approves, regenerates, publishes or deploys.
import { useEffect, useState } from 'react'
import { GLOBAL_ROUTES } from '../tracker/trackerConfig.ts'
import { MASTER_SUMMARIES } from '../rag/CogneePreflightCockpit'
import { Icon, Kpis, StatusPill } from '../components/ui'

// ---- Admin > Masters --------------------------------------------------------------------------------------
const MASTERS = [
  { name: 'Organization', records: MASTER_SUMMARIES.org.length, editable: true },
  { name: 'Rules', records: MASTER_SUMMARIES.rules.length, editable: true },
  { name: 'Agents', records: MASTER_SUMMARIES.agents.length, editable: true },
  { name: 'Skills', records: MASTER_SUMMARIES.skills.length, editable: true },
  { name: 'Filetype', records: MASTER_SUMMARIES.filetypes.length, editable: false },
]

/** The Masters page scrolled to one master (Verify's failed-answer Edit / "Open in … master"), or undefined. */
export const masterLink = (name?: string) => name && MASTERS.some(m => m.name === name) ? `${GLOBAL_ROUTES.admin.path}#master-${name.toLowerCase()}` : undefined

export function MastersPage() {
  // Opened for one master (masterLink): scroll to it and mark its row.
  const [focus] = useState(() => decodeURIComponent(location.hash.slice(1)))
  useEffect(() => { if (focus) document.getElementById(focus)?.scrollIntoView({ block: 'center' }) }, [focus])
  const readme = () => [
    '# BAM Masters', '', 'EXAMPLE fixture data (Manufacturing_Masters_v0.2.xlsx). Masters drive every workflow.', '',
    '| Master | Records | Editable |', '|---|---|---|',
    ...MASTERS.map(m => `| ${m.name} | ${m.records} | ${m.editable ? 'Yes' : 'Frozen: add a new version instead'} |`), '',
  ].join('\n')
  const exportReadme = () => {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([readme()], { type: 'text/markdown' })); a.download = 'BAM-Masters-README.md'; a.click(); URL.revokeObjectURL(a.href)
  }
  return (
    <div className="rag">
      <div className="rag-col">
        <header className="v3-head">
          <h2 id="center-title" tabIndex={-1}>Masters <span className="tag example-tag">EXAMPLE fixture</span></h2>
          <p className="muted sm">Masters drive every workflow. Filetype is frozen: change it by adding a new version, never by editing.</p>
        </header>
        <Kpis label="Masters summary" items={[
          { value: MASTERS.length, label: 'Masters' },
          { value: MASTER_SUMMARIES.org.length, label: 'Org records' },
          { value: MASTER_SUMMARIES.rules.length, label: 'Rules' },
          { value: MASTERS.filter(m => !m.editable).length, label: 'Frozen' },
        ]} />
        <section className="v3-card table-card" aria-labelledby="ms-title">
          <h3 id="ms-title" className="sr-only">Masters</h3>
          <div className="scroll-box">
            <table className="table v3-table">
              <thead><tr><th scope="col">Master</th><th scope="col">Records</th><th scope="col">Editable</th></tr></thead>
              <tbody>{MASTERS.map(m => (
                <tr key={m.name} id={`master-${m.name.toLowerCase()}`} className={focus === `master-${m.name.toLowerCase()}` ? 'ms-hl' : undefined} aria-current={focus === `master-${m.name.toLowerCase()}` || undefined}><th scope="row">{m.name}</th><td>{m.records}</td>
                  <td>{m.editable ? <StatusPill status="pass" text="Yes" /> : <StatusPill status="locked" text="Frozen · new version only" />}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
        <section className="v3-card" aria-labelledby="ms-readme">
          <h3 id="ms-readme" className="v3-card-title"><Icon name="file-text" size={14} />README</h3>
          <pre className="readme">{readme()}</pre>
        </section>
        <div className="row">
          <button className="btn btn-primary" onClick={exportReadme}><Icon name="download" />Export README</button>
          <span className="gate"><button className="btn btn-secondary" aria-disabled aria-describedby="ms-why"><Icon name="edit" />New version</button>
            <span id="ms-why" className="gate-reason"><Icon name="lock" size={12} />New versions can't be created here yet</span></span>
        </div>
      </div>
    </div>
  )
}
