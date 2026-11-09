import { useSyncExternalStore } from 'react'
import Uppy from '@uppy/core'
import { api } from '../api/adapter'
import { ACCEPTED_TYPES, MAX_UPLOAD_BYTES } from '../api/types'
import { initialStatuses, type Statuses } from '../tracker/trackerConfig'
import { localTrackerStorage } from '../tracker/trackerStorage'
import { addFilesLocked } from './goal'

// UI-only state: what the viewer is looking at. Process state lives in the backend snapshot.
export interface Ui {
  theme: 'dark' | 'light'
  env: 'FDE' | 'Staging'
  settingsOpen: boolean
  passage?: string
  codeFocus?: { file: string; line?: number }
  /** Collapsed side panes in the 3-pane workbench screens. */
  collapsed: { left: boolean; right: boolean }
  tracker: TrackerState
  /** Files refused at intake in this session (type/size limits, upload errors), with the reason. Shown in 1.2 and 1.4. */
  rejected: { name: string; reason: string; at: string }[]
  /** Files discarded from Upload in this session (Discard / Delete file / Replace), with the reason. Shown collapsed as Archive in 1.1. Discarding never deletes file data. */
  archived: { name: string; reason: string; at: string }[]
  /** Set by Review's Add files (5 Nov 2026): Upload opens this picker as soon as its intake handler is registered. */
  armPicker?: 'files' | 'folder' | 'zip'
  /** Upload record selected in 1.3 File detail. */
  fileDetail?: string
  /** Skill selected in 3.3 Skills/code review. */
  reviewSkill?: string
  /** FDE Intake Objectives & Scope Directives (typed or Windows Voice-to-Text). */
  fdeGoals?: string
  /** v3 shell: collapsed NAVIGATE and Enterprise Assistant panes (saved as `bam-shell-v2`). */
  shell: { nav: boolean; assistant: boolean }
  /** Narrow screens: which shell pane is open as an overlay drawer. */
  drawer: 'nav' | 'assistant' | null
  /** Usage & improvement filters (kept while you move around the app). */
  usage: { client: string; workflow: string; range: '7d' | '30d' | '90d' }
  /** Improvement notes (Usage & improvement). Proposals only: they never change the workflow. Saved as `bam-improvement-notes`. */
  notes: { id: string; text: string; at: string }[]
  /** Schemas: the schema open on screen (field edits typed in the assistant apply to it). */
  ragSchema?: string
  /** A question a screen puts to the assistant (a graph node click): appended to the thread once per id. */
  assistantAsk?: { id: number; text: string }
}

// Manual progress tracker; lives in the same store the stepper navigates with.
export interface TrackerState {
  /** Manual statuses; shown only while `basis` matches the evidence (effectiveStatuses). */
  statuses: Statuses
  basis: Partial<Statuses>
  /** Workflow evidence per screen (tracker/evidence.ts), not persisted. */
  evidence: Statuses
  /** Screens whose evidence is from a previous RAG run. */
  previous: string[]
  openTask: string | null
  selected: string | null
  /** Edits stay disabled until saved progress has loaded. */
  loaded: boolean
  storageError?: string
}
export const trackerStorage = localTrackerStorage()

const saved = (() => { try { return localStorage.getItem('bam-theme') } catch { return null } })()
const savedPanes = (() => { try { return JSON.parse(localStorage.getItem('bam-panes-v3') ?? 'null') as Ui['collapsed'] | null } catch { return null } })()
const savedGoals = (() => { try { return localStorage.getItem('bam-fde-goals') ?? '' } catch { return '' } })()
// `bam-shell-v2` (21 Oct 2026): the old `bam-shell` held states written by the removed stage auto-collapse, so it is ignored
// and everyone starts expanded; from now on only the person's own Hide / Show choice is saved.
const savedShell = (() => { try { localStorage.removeItem('bam-shell'); return JSON.parse(localStorage.getItem('bam-shell-v2') ?? 'null') as Ui['shell'] | null } catch { return null } })()
const savedNotes = (() => { try { return JSON.parse(localStorage.getItem('bam-improvement-notes') ?? '[]') as Ui['notes'] } catch { return [] } })()
let state: Ui = {
  // v3: light is the default visual target; a saved choice (dark or light) still wins.
  theme: saved === 'light' || saved === 'dark' ? saved : 'light',
  env: 'FDE', settingsOpen: false,
  // v3: the 3 panes sit inside Results & edits, so the side panes start collapsed (rails) until the user opens them.
  collapsed: { left: savedPanes ? savedPanes.left : true, right: savedPanes ? savedPanes.right : true },
  tracker: { statuses: initialStatuses(), basis: {}, evidence: initialStatuses(), previous: [], openTask: null, selected: null, loaded: false },
  rejected: [],
  archived: [],
  fdeGoals: savedGoals,
  shell: { nav: savedShell?.nav === true, assistant: savedShell?.assistant === true },
  drawer: null,
  usage: { client: 'all', workflow: 'all', range: '30d' },
  notes: Array.isArray(savedNotes) ? savedNotes : [],
}
const subs = new Set<() => void>()

/** No notification UI (removed 9 Oct 2026): ported repo code still calls toast(); failures are logged, the rest is dropped. */
export function toast(text: string, tone: 'info' | 'success' | 'error' = 'info', _screen?: string, _key?: string) {
  if (tone === 'error') console.error(text)
}

export const openSettings = () => setUi({ settingsOpen: true })
export const closeSettings = () => setUi({ settingsOpen: false })

export function setUi(patch: Partial<Ui> | ((s: Ui) => Partial<Ui>)) {
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
  try {
    localStorage.setItem('bam-theme', state.theme)
    localStorage.setItem('bam-panes-v3', JSON.stringify(state.collapsed))
    localStorage.setItem('bam-shell-v2', JSON.stringify(state.shell))
    localStorage.setItem('bam-improvement-notes', JSON.stringify(state.notes))
    if (state.fdeGoals !== undefined) localStorage.setItem('bam-fde-goals', state.fdeGoals)
  } catch { /* storage blocked */ }
  subs.forEach(f => f())
}

export const getUi = () => state

export const useUi = <T,>(select: (s: Ui) => T) =>
  useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => select(state))

void trackerStorage.load().then(r => setUi(s => ({ tracker: { ...s.tracker, statuses: r.statuses, basis: r.basis, loaded: true, storageError: r.error } })))

// One Uppy instance for the app's lifetime, so uploads keep running when the modal closes.
export const uppy = new Uppy({
  id: 'bam-intake',
  restrictions: { maxFileSize: MAX_UPLOAD_BYTES, allowedFileTypes: ACCEPTED_TYPES },
})

uppy.addUploader(async fileIDs => {
  const files = fileIDs.map(id => uppy.getFile(id))
  uppy.emit('upload-start', files)
  // ponytail: 3 files at a time so a big batch never floods the server; raise if the backend scales.
  const queue = [...files]
  const worker = async () => { for (let f = queue.shift(); f; f = queue.shift()) await send(f) }
  await Promise.all([worker(), worker(), worker()])
})

async function send(f: ReturnType<typeof uppy.getFile>) {
    try {
      const batchId = (f.meta as { batchId?: string } | undefined)?.batchId
      const rec = await api.uploadFile({ name: f.name ?? 'file', size: f.size ?? 0, type: f.type, data: f.data as Blob, batchId }, (loaded, total) =>
        uppy.emit('upload-progress', uppy.getFile(f.id), { uploadStarted: Date.now(), bytesUploaded: loaded, bytesTotal: total }))
      uppy.emit('upload-success', uppy.getFile(f.id), { status: 200, body: rec as never, uploadURL: rec.objectKey })
    } catch (e) {
      uppy.emit('upload-error', uppy.getFile(f.id), { name: 'UploadError', message: (e as Error).message })
    }
}

/** Browse/drop in 1.1: add files to the shared Uppy instance (type/size restrictions apply) and upload right away. */
export function uploadFiles(list: FileList | File[], batchId?: string) {
  if (addFilesLocked()) return // the last guard: nothing uploads before the goal is approved
  const done = uppy.getFiles().filter(f => f.progress.uploadComplete).map(f => f.id)
  if (done.length) uppy.removeFiles(done)
  uppy.setOptions({ restrictions: { maxNumberOfFiles: null } })
  let added = 0
  for (const f of list) {
    try {
      uppy.addFile({
        name: f.name,
        type: f.type,
        data: f,
        source: 'intake',
        meta: { batchId: (f as { batchId?: string }).batchId || batchId },
      })
      added++
    } catch { /* restriction-failed already reported */ }
  }
  if (added) void uppy.upload()
}

const reject = (name: string, reason: string) => setUi(s => ({ rejected: [...s.rejected, { name, reason, at: new Date().toISOString() }] }))
/** Archive a discarded file (5 Nov 2026): the row leaves the list, its name and reason stay visible under Archive. File data is never deleted. */
export const archive = (name: string, reason: string) => setUi(s => ({ archived: [...s.archived, { name, reason, at: new Date().toISOString() }] }))
// Refused and failed files are listed in 1.1/1.2/1.4 (no notices, removed 9 Oct 2026).
uppy.on('restriction-failed', (file, err) => reject(file?.name ?? 'File', err.message))
uppy.on('upload-error', (file, err) => { if (file) reject(file.name ?? 'File', err?.message ?? 'Upload failed') })
