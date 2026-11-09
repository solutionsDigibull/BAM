// Pure presentation rules for the shared BAM action button and the 4-stage tracker (tested in bamUi.test.ts).
import type { TrackerStatus } from '../tracker/trackerConfig'

export type BamAction = 'ingest' | 'generate' | 'validate' | 'deploy' | 'approve' | 'save' | 'review' | 'commit'
export const BAM_ACTIONS: Record<BamAction, { icon: string; loading: string; success: string }> = {
  ingest: { icon: 'ingest', loading: 'Ingesting…', success: 'Ingested' },
  generate: { icon: 'sparkle', loading: 'Generating…', success: 'Generated' },
  validate: { icon: 'check', loading: 'Validating…', success: 'Validated' },
  deploy: { icon: 'rocket', loading: 'Deploying…', success: 'Deployed' },
  approve: { icon: 'doccheck', loading: 'Approving…', success: 'Approved' },
  save: { icon: 'save', loading: 'Saving…', success: 'Saved' },
  review: { icon: 'eye', loading: 'Signing off…', success: 'Signed off' },
  commit: { icon: 'db', loading: 'Committing…', success: 'Committed' },
}
export const SUCCESS_MS = 1800

/** Operation state. `busy` = real in-flight/running state from the owner; `flash` = the brief success display;
 *  `blocked` = an eligibility rule. Any of them locks the button; the flash never re-enables a blocked one. */
export function actionView({ busy, flash, blocked }: { busy?: boolean; flash?: boolean; blocked?: boolean }) {
  const phase = busy ? 'loading' : flash ? 'success' : 'idle'
  return { phase, locked: !!(busy || flash || blocked) } as const
}

/** Success shows only when the confirmed result key changes to a new value (never on mount, never on a click). */
export const isNewSuccess = (prev: unknown, next: unknown) => next != null && next !== false && next !== prev

export interface StageMarker { status: TrackerStatus; current: boolean }
/**
 * Stage markers from the evidence rollups (in stage order). The execution-current stage is the first one not done,
 * once any work exists; none when nothing started or everything is done. A connector is earned only when the stage
 * it leaves is done and the next stage has evidence of its own.
 */
export function stageMarkers(statuses: readonly TrackerStatus[]) {
  const idle = statuses.every(s => s === 'not_started')
  const current = idle ? -1 : statuses.findIndex(s => s !== 'done')
  const markers: StageMarker[] = statuses.map((status, i) => ({ status, current: i === current }))
  const earned = statuses.slice(0, -1).map((s, i) => s === 'done' && statuses[i + 1] !== 'not_started')
  return { current, markers, earned }
}

/** "1 file", "2 files"; pass the plural form when it is not just "+s" ("1 batch", "2 batches"). */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
