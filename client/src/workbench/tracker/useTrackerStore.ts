import { useMemo } from 'react'
import { SUBTASKS, effectiveStatuses, type TrackerStatus } from './trackerConfig.ts'
import { getUi, setUi, trackerStorage, useUi, type TrackerState } from '../lib/store'

export const useTrackerStore = <T,>(select: (t: TrackerState) => T) => useUi(s => select(s.tracker))

/** What every tracker view shows and counts (one rule: effectiveStatuses). */
export const useStatuses = () => {
  const t = useUi(s => s.tracker)
  return useMemo(() => effectiveStatuses(t.evidence, t.statuses, t.basis), [t.evidence, t.statuses, t.basis])
}

export const setTracker = (patch: Partial<TrackerState>) => setUi(s => ({ tracker: { ...s.tracker, ...patch } }))

/**
 * Set one screen's manual status and persist it, pinned to the screen's current evidence: it shows until that
 * evidence changes. Real execution, approval, certification and deployment come from backend evidence, never from this.
 */
export async function updateSubtask(id: string, status: TrackerStatus) {
  if (!SUBTASKS.some(s => s.id === id)) throw new Error(`Unknown screen ${id}`)
  if (!getUi().tracker.loaded) throw new Error('Tracker progress is still loading')
  setUi(s => ({ tracker: { ...s.tracker, statuses: { ...s.tracker.statuses, [id]: status }, basis: { ...s.tracker.basis, [id]: s.tracker.evidence[id] } } }))
  try {
    await trackerStorage.save(getUi().tracker.statuses, getUi().tracker.basis)
    if (getUi().tracker.storageError) setTracker({ storageError: undefined })
  } catch (e) {
    setTracker({ storageError: (e as Error).message })
  }
}
