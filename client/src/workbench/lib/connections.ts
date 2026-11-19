// The project's secrets and connections (#764, ADR 0030) as the Validation screen shows them: names, status, the masked
// tail and the last test, never a value. A value is typed into a password field, sent once and forgotten here.
import { useCustom } from '@refinedev/core'
import type { Connection, ConnectionList, ConnectionStatus, ConnectionTestOutcome } from '@/contracts'
import type { Tone } from './workflowCommit'

export const STATUS_LABEL: Record<ConnectionStatus, string> = {
  MISSING: 'Missing', SET: 'Set, not tested', TESTED: 'Tested', TEST_FAILED: 'Test failed',
}
export const statusTone = (s: ConnectionStatus): Tone => (s === 'TESTED' ? 'ok' : s === 'TEST_FAILED' ? 'err' : s === 'SET' ? 'warn' : 'muted')

export const OUTCOME_TEXT: Record<ConnectionTestOutcome, string> = {
  OK: 'The service accepted this key.',
  UNAUTHORIZED: 'The service rejected this key.',
  NOT_FOUND: 'The service answered "not found" at this address.',
  RATE_LIMITED: 'The service is rate limiting this key.',
  SERVER_ERROR: 'The service had an error.',
  REDIRECT: 'The service redirected the call; redirects are not followed.',
  UNREACHABLE: 'The service could not be reached.',
  TIMEOUT: 'The service did not answer in time.',
  TLS_ERROR: 'The secure connection failed.',
  BLOCKED_HOST: 'This address is not allowed (private or internal).',
}

/** The `If-Match` of an entry that is stored; a name that has no row yet sends none. */
export const ifMatchOf = (c: Pick<Connection, 'row_version'>): string | undefined => (c.row_version ? `"${c.row_version}"` : undefined)

/** "ends 1716", or nothing when the secret is too short to show any part of. */
export const tailText = (c: Pick<Connection, 'masked_tail'>): string => (c.masked_tail ? `ends ${c.masked_tail}` : 'value hidden')

/** The test needs a stored value and either a target address or the FDE's "no remote service" statement. */
export function testReason(c: Connection): string | undefined {
  if (c.status === 'MISSING') return 'Set a value first'
  if (c.test_mode === 'NONE') return 'This secret has no remote service to test'
  if (!c.test_config?.url) return 'Save a test address first'
  return undefined
}

export function useConnections() {
  const { query } = useCustom<ConnectionList>({ url: 'connections', method: 'get', queryOptions: { retry: false } })
  return { data: query.data?.data, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}
