// Upload: the recycle bin, under the file results. A removed file stays 90 days; Restore takes it back and the backend scans and
// reads it again. The server decides what can be restored (410 purged, 409 file limit): its reason is shown as it came.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Snapshot } from '../api/types'
import { api } from '../api/adapter'
import { restoreReason } from '../http/documents'
import { Icon, fmtBytes } from '../components/ui'

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

export function RecycleBin({ snap, hideWhenEmpty }: { snap: Snapshot; hideWhenEmpty?: boolean }) {
  const qc = useQueryClient()
  const editable = snap.steps[0]!.editable
  const deleted = snap.deleted ?? []
  const [busy, setBusy] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const restore = async (id: string) => {
    setBusy(id)
    setErrors(e => { const { [id]: _drop, ...rest } = e; return rest })
    try {
      await api.restoreUpload(id)
      await qc.invalidateQueries()
    } catch (e) {
      setErrors(x => ({ ...x, [id]: restoreReason(e) }))
    } finally {
      setBusy(null)
    }
  }

  if (hideWhenEmpty && !deleted.length) return null
  return (
    <section className="dc-rows" aria-label="Recycle bin">
      <details open={deleted.length > 0}>
        <summary><strong>Recycle bin</strong><span className="dc-count">{deleted.length}</span><Icon name="down" size={13} /></summary>
        {!deleted.length ? <p className="muted sm dc-none">No removed files. A removed file stays here for 90 days.</p> : (
          <ul className="dc-list" aria-label="Removed files">
            {deleted.map(d => (
              <li key={d.id}>
                <Icon name="file" size={15} />
                <div>
                  <strong className="dc-name">{d.name}</strong>
                  <span className="muted xs">{fmtBytes(d.size)} · removed {day(d.deletedAt)}{d.purgeAfter ? ` · kept until ${day(d.purgeAfter)}` : ''}</span>
                  {errors[d.id] && <span className="xs tone-err" role="alert">{errors[d.id]}</span>}
                </div>
                {editable
                  ? <button className="btn btn-outline sm" disabled={busy !== null} onClick={() => { void restore(d.id) }}><Icon name="refresh" />{busy === d.id ? 'Restoring…' : 'Restore'}</button>
                  : <span className="dc-pill tone-info">Read only</span>}
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  )
}
