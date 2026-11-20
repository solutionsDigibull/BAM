// Upload and Standardize: the Open action of a document row (#895). It asks the API for a short-lived signed link to the
// stored original and follows it; the file is always an attachment, so the browser saves it and never renders it in the app.
// Only a released (clean) file has a link: for any other the button is disabled and says why. A refused request shows the
// server's reason in place. A link that already ran out (a slow device, a skewed clock) is asked for once more.
import { useId, useState } from 'react'
import type { UploadRecord } from '../api/types'
import { api } from '../api/adapter'
import { accessReason, openBlock } from '../http/documents'
import { Icon } from '../components/ui'

/** Links this close to their end are not followed: the browser could be a moment too late. */
export const EXPIRY_MARGIN_MS = 5000

export async function openFile(id: string, now: () => number = Date.now): Promise<void> {
  const live = (a: { expiresAt: string }) => Date.parse(a.expiresAt) - now() > EXPIRY_MARGIN_MS
  let access = await api.getVerifiedFileAccess(id)
  if (!live(access)) access = await api.getVerifiedFileAccess(id)
  if (!live(access)) throw new Error('The link ran out before it could be opened. Check the clock of this device and try again.')
  const a = document.createElement('a')
  a.href = access.url
  a.target = '_blank'
  a.rel = 'noopener noreferrer'
  a.click()
}

interface OpenFileProps {
  upload: UploadRecord
  className?: string
  /** An icon-only button for a narrow table cell: the label stays for assistive technology and as a tooltip. */
  compact?: boolean
  /** The caller shows the refusal (a table shows it on a row of its own); without it the reason is shown under the button. */
  report?: (message?: string) => void
}

export function OpenFileButton({ upload, className = 'btn btn-outline sm', compact, report }: OpenFileProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const why = useId()
  const block = openBlock(upload)
  const say = (message?: string) => (report ? report(message) : setError(message))
  const open = async () => {
    setBusy(true)
    say(undefined)
    try {
      await openFile(upload.id)
    } catch (e) {
      say(accessReason(e))
    } finally {
      setBusy(false)
    }
  }
  const label = busy ? 'Opening…' : 'Open'
  return (
    <span className="of">
      <button type="button" className={compact ? 'icon-btn sm' : className} disabled={!!block || busy} title={block ?? (compact ? 'Open' : undefined)}
        aria-label={`Open ${upload.name}`} aria-busy={busy || undefined} aria-describedby={block ? why : undefined} onClick={() => { void open() }}>
        <Icon name={busy ? 'spinner' : 'tb-arrow-down'} size={14} spin={busy} />{!compact && label}
        {compact && busy && <span className="sr-only">{label}</span>}
      </button>
      {block && <span id={why} className="sr-only">{block}</span>}
      {error && <span className="of-err xs tone-err" role="alert">{error}</span>}
    </span>
  )
}
