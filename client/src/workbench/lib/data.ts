import { useState } from 'react'
import { useCustom, type BaseRecord } from '@refinedev/core'
import { useQueryClient } from '@tanstack/react-query'
import type { CodeFile, SkillRow, Snapshot, StepId } from '../api/types'

// A snapshot is three API calls through the Next proxy, so a 1 s poll kept the dev server and the API queued behind it and
// made every click slow. Commands refresh it at once (useAction invalidates), so the poll only has to catch background work.
export const SNAPSHOT_POLL_MS = 4000
// Nothing is running, so nothing changes behind the user's back: poll slowly and leave the API to what they click.
export const SNAPSHOT_IDLE_POLL_MS = 15000

/** True while the backend works on its own (a scan, a run, a build), so the snapshot can change without a click. */
export function backendBusy(snap: Snapshot | undefined): boolean {
  if (!snap) return true // first load or after an error: keep trying quickly
  const running = (s: { status: string }) => s.status === 'queued' || s.status === 'running'
  return snap.steps.some(s => s.background > 0 || (s.state === 'active' && !s.editable))
    || snap.sowGeneration.some(running) || snap.extraction.some(running)
}

export const snapshotPollMs = (snap: Snapshot | undefined): number => (backendBusy(snap) ? SNAPSHOT_POLL_MS : SNAPSHOT_IDLE_POLL_MS)

// One shared poll of the backend snapshot; every status in the UI derives from it. React Query pauses it in a hidden tab
// (the background-poll option stays off) and the interval follows the work: fast while the backend is busy, slow when idle.
export function useSnapshot() {
  const { query } = useCustom<Snapshot & BaseRecord>({ url: 'snapshot', method: 'get', queryOptions: { refetchInterval: q => snapshotPollMs(q.state.data?.data as Snapshot | undefined), retry: false } })
  return { snap: query.data?.data as Snapshot | undefined, error: query.error, isLoading: query.isLoading, syncedAt: query.dataUpdatedAt, refetch: query.refetch }
}

// `rev` changes when the backend says the data changed, which re-keys the query.
export function useSkills(rev: number) {
  const { query } = useCustom<{ rows: SkillRow[] }>({ url: 'skills', method: 'get', config: { query: { rev } }, queryOptions: { placeholderData: prev => prev } })
  return { rows: query.data?.data.rows, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

export function useCode(rev: string) {
  // Keep showing the previous result while a new revision loads, so editors never unmount mid-edit.
  const { query } = useCustom<{ files: CodeFile[] }>({ url: 'code', method: 'get', config: { query: { rev } }, queryOptions: { placeholderData: prev => prev } })
  return { files: query.data?.data.files, error: query.error, isLoading: query.isLoading, refetch: query.refetch }
}

// Runs a backend command, then refreshes reads. Failures are logged (no notices, removed 9 Oct 2026); the screen's own
// state (button, status, gate reason) shows what happened, and the backend keeps refusing invalid operations.
export function useAction() {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  async function run<T>(label: string, fn: () => Promise<T>, opts: { success?: string; step?: StepId } = {}): Promise<T | undefined> {
    setBusy(label)
    try {
      return await fn()
    } catch (e) {
      console.error(`${label} failed:`, (e as Error).message)
    } finally {
      setBusy(null)
      void qc.invalidateQueries()
    }
  }
  return { busy, run }
}
