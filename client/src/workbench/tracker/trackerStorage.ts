import { SUBTASKS, TRACKER_STATUSES, initialStatuses, type Statuses, type TrackerStatus } from './trackerConfig.ts'

/** Namespace for the 16-screen structure. */
export const TRACKER_KEY = 'bullm-bam.tracker16.v1'
/** The old 21-subtask data. Never read, migrated, overwritten or deleted. */
export const LEGACY_TRACKER_KEY = 'bullm-bam.tracker.v1'
const VERSION = 1

export interface LoadResult {
  statuses: Statuses
  /** For each manual status, the evidence status it overrode; the override lapses when the evidence changes. */
  basis: Partial<Statuses>
  /** Set when saved progress could not be read; the UI must show it. */
  error?: string
}

// The only seam for tracker persistence. Async so a server can replace it without changing callers.
// TODO: replace with a PostgreSQL-backed adapter (e.g. GET/PUT /api/tracker) once that endpoint exists.
// Until then progress is same-device only: not shared, not synced.
export interface TrackerStorage {
  load(): Promise<LoadResult>
  /** Rejects with a readable error when the write fails. */
  save(statuses: Statuses, basis?: Partial<Statuses>): Promise<void>
}

type KeyValue = Pick<Storage, 'getItem' | 'setItem'>

function browserStorage(): KeyValue | undefined {
  try { return globalThis.localStorage } catch { return undefined }
}

const isStatus = (v: unknown): v is TrackerStatus => typeof v === 'string' && (TRACKER_STATUSES as readonly string[]).includes(v)
const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function localTrackerStorage(store: KeyValue | undefined = browserStorage()): TrackerStorage {
  return {
    async load() {
      const statuses = initialStatuses()
      if (!store) return { statuses, basis: {}, error: 'Browser storage is unavailable, so progress will not be kept after a reload.' }
      let raw: string | null
      try { raw = store.getItem(TRACKER_KEY) } catch (e) {
        return { statuses, basis: {}, error: `Saved progress could not be read (${message(e)}).` }
      }
      if (raw === null) return { statuses, basis: {} }
      const unreadable = { statuses, basis: {}, error: 'Saved progress is unreadable; starting from Not started. The stored data was left in place.' }
      let parsed: unknown
      try { parsed = JSON.parse(raw) } catch { return unreadable }
      if (typeof parsed !== 'object' || parsed === null || (parsed as { version?: unknown }).version !== VERSION) return unreadable
      const saved = (parsed as { statuses?: unknown }).statuses
      if (typeof saved !== 'object' || saved === null) return unreadable
      // Restore by stable screen ID; unknown IDs and invalid values are ignored.
      for (const s of SUBTASKS) {
        const v = (saved as Record<string, unknown>)[s.id]
        if (isStatus(v)) statuses[s.id] = v
      }
      // Without a basis (older saves) a manual status never overrides evidence.
      const basis: Partial<Statuses> = {}
      const b = (parsed as { basis?: Record<string, unknown> }).basis
      for (const s of SUBTASKS) if (isStatus(b?.[s.id])) basis[s.id] = b![s.id] as TrackerStatus
      return { statuses, basis }
    },
    async save(statuses, basis = {}) {
      if (!store) throw new Error('Browser storage is unavailable')
      try { store.setItem(TRACKER_KEY, JSON.stringify({ version: VERSION, statuses, basis })) } catch (e) {
        throw new Error(`Progress could not be saved on this device (${message(e)}).`)
      }
    },
  }
}
