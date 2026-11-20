import { useCustom } from '@refinedev/core'
import type { LlmUsage, Preflight, PreflightCheck } from '@/contracts'
import { Banner, Dialog, Empty, ErrorState, Skeleton, StatusPill, Time } from './ui'

// System Settings (#826, part 1): what BAM can tell the FDE about its engines today, read only. Everything comes from the
// backend: `GET /preflight` (the platform services and the model providers, cached a minute by the API) and
// `GET /projects/{p}/llm-usage` (what this project spent, by engine alias). BAM_UI's quarantine gate, Repel shield and
// SOW accuracy evaluation have no settings API yet, so they are not shown (see #826); nothing here is simulated.

export const ENGINE_NAMES: Record<string, string> = {
  'sbam-knowledge': 'Knowledge graph',
  'sbam-embed': 'Embeddings',
  'sbam-relevance': 'Relevance scoring',
  'sbam-sow': 'SOW',
  'sbam-ocr': 'OCR',
  'sbam-isatvon': 'Workflow design',
  'sbam-codegen': 'Code generation',
  'sbam-testgen': 'Test generation',
  'sbam-corrector': 'Auto-correction',
}
const engineName = (alias: string) => ENGINE_NAMES[alias] ?? alias

const STATUS_TEXT: Record<PreflightCheck['status'], string> = { ok: 'Ready', mock: 'Mock (synthetic results)', degraded: 'Degraded', down: 'Down' }
const STATUS_PILL: Record<PreflightCheck['status'], 'pass' | 'pending' | 'fail'> = { ok: 'pass', mock: 'pending', degraded: 'pending', down: 'fail' }

type Failure = { statusCode?: number; problem?: { detail?: string }; message?: string }
const failed = (e: unknown) => {
  const f = e as Failure | null
  return f?.statusCode === 403
    ? <ErrorState title="You can't see these settings">Your account has no access to them.</ErrorState>
    : <ErrorState title="Could not load">{f?.problem?.detail ?? f?.message ?? 'The request failed.'}</ErrorState>
}

function Engines() {
  const { query } = useCustom<Preflight>({ url: 'preflight', method: 'get', queryOptions: { retry: false } })
  const data = query.data?.data
  if (query.error) return failed(query.error)
  if (query.isLoading || !data) return <Skeleton rows={3} label="Checking the engines" />
  if (data.checks.length === 0) return <Empty icon="info" title="No checks reported">The platform reported no services to check.</Empty>
  const mock = data.checks.some(c => c.status === 'mock')
  return <>
    {mock && <Banner tone="info" icon="info"><strong>The mock model is on.</strong> Results are synthetic; this is a test setup.</Banner>}
    <table className="table" aria-label="Engines and services">
      <thead><tr><th scope="col">Engine or service</th><th scope="col">Status</th><th scope="col">What to do</th></tr></thead>
      <tbody>{data.checks.map(c => (
        <tr key={c.id} data-check={c.id}>
          <td>{c.name}</td>
          <td><StatusPill status={STATUS_PILL[c.status]} text={STATUS_TEXT[c.status]} /></td>
          <td className="muted xs">{c.status === 'ok' ? '—' : c.fix ?? c.reason ?? '—'}</td>
        </tr>
      ))}</tbody>
    </table>
    <p className="muted xs">Checked <Time iso={data.checked_at} />.</p>
  </>
}

function Usage() {
  const { query } = useCustom<LlmUsage>({ url: 'llm-usage', method: 'get', queryOptions: { retry: false } })
  const data = query.data?.data
  if (query.error) return failed(query.error)
  if (query.isLoading || !data) return <Skeleton rows={3} label="Loading the model usage" />
  if (data.calls === 0) return <Empty icon="info" title="No model calls yet">This project has not used an engine yet.</Empty>
  return (
    <table className="table" aria-label="Model usage by engine">
      <thead><tr><th scope="col">Engine</th><th scope="col">Calls</th><th scope="col">Failed</th><th scope="col">Tokens in / out</th><th scope="col">Cost (USD)</th></tr></thead>
      <tbody>
        {data.stages.map(s => (
          <tr key={s.alias}>
            <td>{engineName(s.alias)} <span className="mono xs muted">{s.alias}</span></td>
            <td>{s.calls}</td><td>{s.failed}</td><td>{s.tokens_in} / {s.tokens_out}</td><td className="mono">{s.cost_usd}</td>
          </tr>
        ))}
        <tr><th scope="row">Total</th><td>{data.calls}</td><td /><td>{data.tokens_in} / {data.tokens_out}</td><td className="mono">{data.total_usd}</td></tr>
      </tbody>
    </table>
  )
}

/** The dialog the profile menu opens. Read only: engines and models are set by the platform admin, not here. */
export function SystemSettings({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} title="System settings" onClose={onClose} footer={<button className="btn btn-primary" onClick={onClose}>Close</button>}>
      <p className="muted sm">Read only. The engines and models are set by the platform admin; nothing here changes them.</p>
      <section aria-label="Engines">
        <h3>Engines</h3>
        {open && <Engines />}
      </section>
      <section aria-label="Model usage">
        <h3>Model usage in this project</h3>
        {open && <Usage />}
      </section>
    </Dialog>
  )
}
