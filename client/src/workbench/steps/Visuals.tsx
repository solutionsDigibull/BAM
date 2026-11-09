// Scope › Visuals (21 Oct 2026): images attached alongside the SOW, edited with Filerobot Image Editor. No visuals
// contract exists in BamApi, so the list lives in this browser tab only (memory, object URLs); nothing is uploaded.
// Save never overwrites: the edited image is added as a new "Edited copy" item, and the original stays as it was.
import { Component, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import FilerobotImageEditor, { TABS, TOOLS, type getCurrentImgDataFunction } from 'react-filerobot-image-editor'
import { Banner, Icon } from '../components/ui'

export interface Visual { id: string; name: string; url: string; type: string; width: number; height: number; addedAt: string; editedFrom?: string }

// PNG, JPEG and WebP only: Filerobot exports through a canvas, and SVG/GIF/HEIC would not round-trip.
const ACCEPT: Record<string, string> = { 'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp' }

// ---- session store (one list for the tab; survives navigation, not a reload) ----
let visuals: Visual[] = []
const subs = new Set<() => void>()
const setVisuals = (next: Visual[]) => { visuals = next; subs.forEach(f => f()) }
const useVisuals = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => visuals)
const uid = () => crypto.randomUUID()

function measure(url: string): Promise<{ width: number; height: number }> {
  const img = new Image()
  img.src = url
  return img.decode().then(() => ({ width: img.naturalWidth, height: img.naturalHeight }))
}

async function addFiles(files: File[]): Promise<string[]> {
  const errors: string[] = []
  const added: Visual[] = []
  for (const f of files) {
    if (!ACCEPT[f.type]) { errors.push(`${f.name}: not a supported image. Use PNG, JPEG or WebP.`); continue }
    const url = URL.createObjectURL(f)
    try {
      added.push({ id: uid(), name: f.name, url, type: f.type, ...(await measure(url)), addedAt: new Date().toISOString() })
    } catch {
      URL.revokeObjectURL(url)
      errors.push(`${f.name}: the image could not be read. It may be damaged.`)
    }
  }
  if (added.length) setVisuals([...visuals, ...added])
  return errors
}

function removeVisual(v: Visual) {
  URL.revokeObjectURL(v.url)
  setVisuals(visuals.filter(x => x.id !== v.id))
}

/** "diagram.png" → "diagram (edited)", then "(edited 2)" … so every copy keeps a distinct, readable name. */
function copyName(source: Visual) {
  const stem = source.name.replace(/\.[^.]+$/, '').replace(/ \(edited(?: \d+)?\)$/, '')
  const taken = new Set(visuals.map(v => v.name.replace(/\.[^.]+$/, '')))
  let n = 1, name = `${stem} (edited)`
  while (taken.has(name)) name = `${stem} (edited ${++n})`
  return name
}

// ---- panel (below the SOW document) ----
export function VisualsPanel() {
  const list = useVisuals()
  const [errors, setErrors] = useState<string[]>([])
  const [editing, setEditing] = useState<Visual | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const byId = new Map(list.map(v => [v.id, v]))

  const pick = async (files: FileList | null) => {
    if (!files?.length) return
    setErrors(await addFiles([...files]))
    if (input.current) input.current.value = ''
  }

  return (
    <section className="visuals" aria-labelledby="visuals-title">
      <div className="visuals-head">
        <h3 id="visuals-title">Visuals</h3>
        <span className="muted xs">{list.length ? 'Kept in this browser tab only, not uploaded' : 'No visuals yet · PNG, JPEG or WebP, kept in this tab only'}</span>
        <button className="btn btn-secondary" onClick={() => input.current?.click()}><Icon name="tb-photo" />Add image</button>
        <input ref={input} type="file" accept={Object.keys(ACCEPT).join(',')} multiple hidden onChange={e => void pick(e.target.files)} />
      </div>
      {errors.length > 0 && <Banner tone="err" icon="alert"
        action={<button className="icon-btn sm" aria-label="Dismiss" onClick={() => setErrors([])}><Icon name="tb-x" size={14} /></button>}>
        {errors.map(e => <div key={e}>{e}</div>)}
      </Banner>}
      {list.length > 0 && <ul className="visuals-list">
          {list.map(v => {
            const src = v.editedFrom ? byId.get(v.editedFrom) : undefined
            return <li key={v.id} className="visual-row">
              <img src={v.url} alt="" width={56} height={42} />
              <div className="visual-meta">
                <span className="visual-name">{v.name}</span>
                <span className="muted xs">
                  {v.editedFrom && <><span className="visual-tag">Edited copy</span>{' '}</>}
                  {v.editedFrom ? `of ${src?.name ?? 'a removed image'} · ` : ''}{v.width} × {v.height}
                </span>
              </div>
              <button className="btn btn-secondary" onClick={() => setEditing(v)} aria-label={`Edit ${v.name}`}><Icon name="tb-pencil" />Edit</button>
              <button className="icon-btn" onClick={() => removeVisual(v)} aria-label={`Remove ${v.name}`} title="Remove"><Icon name="tb-trash" size={15} /></button>
            </li>
          })}
        </ul>}
      {editing && <ImageEditorDialog visual={editing} onDone={() => setEditing(null)} />}
    </section>
  )
}

// ---- editor dialog ----
// A fixed overlay, not a native modal <dialog>: Filerobot portals its menus and colour pickers to <body>, which a
// top-layer dialog would cover. The app root is made inert while it is open, and focus returns to the trigger.

/** Filerobot's theme from the live app tokens (FDE/Staging, light/dark), resolved to real colours for the canvas. */
function editorTheme() {
  const probe = document.createElement('span')
  document.body.append(probe)
  const color = (token: string) => { probe.style.color = `var(${token})`; return getComputedStyle(probe).color }
  const t = {
    accent: color('--accent'), ink: color('--accent-ink'), soft: color('--accent-soft'), bg: color('--bg'),
    surface: color('--surface'), surface2: color('--surface-2'), text: color('--text'), muted: color('--muted'),
    border: color('--border'), strong: color('--border-strong'), hover: color('--hover'),
  }
  probe.remove()
  return {
    palette: {
      'accent-primary': t.accent, 'accent-primary-hover': t.accent, 'accent-primary-active': t.accent, 'accent-stateless': t.accent,
      'bg-primary': t.surface2, 'bg-secondary': t.surface, 'bg-stateless': t.surface, 'bg-primary-active': t.soft, 'bg-active': t.soft,
      'bg-hover': t.hover, 'bg-primary-hover': t.hover, 'txt-primary': t.text, 'txt-secondary': t.muted, 'txt-primary-invert': t.ink,
      'icon-primary': t.text, 'icons-secondary': t.muted, 'borders-primary': t.strong, 'borders-secondary': t.border,
      'borders-strong': t.strong, 'light-shadow': 'rgba(0, 0, 0, .18)',
    },
    typography: { fontFamily: getComputedStyle(document.body).fontFamily },
  }
}

class EditorBoundary extends Component<{ children: ReactNode }, { error?: string }> {
  state: { error?: string } = {}
  static getDerivedStateFromError(e: Error) { return { error: e.message || 'The editor stopped unexpectedly.' } }
  render() {
    return this.state.error
      ? <div className="fie-failed"><Banner tone="err" icon="alert">The image editor could not run: {this.state.error} Close it and try again.</Banner></div>
      : this.props.children
  }
}

function ImageEditorDialog({ visual, onDone }: { visual: Visual; onDone: () => void }) {
  const [dirty, setDirty] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()
  const getImg = useRef<getCurrentImgDataFunction | undefined>(undefined)
  const box = useRef<HTMLDivElement>(null)
  const theme = useMemo(editorTheme, [])

  // Modal behaviour: the app behind is inert; focus goes in, and back to the trigger on close.
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null
    const root = document.getElementById('root')
    root?.setAttribute('inert', '')
    box.current?.focus()
    return () => {
      root?.removeAttribute('inert')
      ;(trigger?.isConnected ? trigger : document.getElementById('center-title'))?.focus()
    }
  }, [])

  // Cancel discards edits; with unsaved edits it asks first, never silently.
  const cancel = () => (dirty && !confirm ? setConfirm(true) : onDone())
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      // Escape inside one of Filerobot's own menus closes that menu only.
      if (e.key !== 'Escape' || !box.current?.contains(e.target as Node)) return
      e.preventDefault()
      if (confirm) setConfirm(false); else cancel()
    }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  })

  const save = async () => {
    const fn = getImg.current
    if (!fn) { setError('The editor is still loading. Try again in a moment.'); return }
    setSaving(true); setError(undefined)
    const ext = ACCEPT[visual.type] ?? 'png'
    const name = copyName(visual)
    let hide: (() => void) | undefined
    try {
      const { imageData, hideLoadingSpinner } = fn({ name, extension: ext }, undefined, true)
      hide = hideLoadingSpinner
      const blob = imageData.imageCanvas
        ? await new Promise<Blob | null>(r => imageData.imageCanvas!.toBlob(r, imageData.mimeType, imageData.quality))
        : imageData.imageBase64 ? await (await fetch(imageData.imageBase64)).blob() : null
      if (!blob) throw new Error('The editor returned no image.')
      const url = URL.createObjectURL(blob)
      const size = await measure(url).catch(() => ({ width: imageData.width ?? 0, height: imageData.height ?? 0 }))
      setVisuals([...visuals, { id: uid(), name: `${name}.${imageData.extension || ext}`, url, type: blob.type || visual.type, ...size, addedAt: new Date().toISOString(), editedFrom: visual.id }])
      onDone()
    } catch (e) {
      setError(`Not saved: ${(e as Error).message || 'the edited image could not be exported.'} Your edits are still here.`)
      setSaving(false)
    } finally { hide?.() }
  }

  return createPortal(
    <div className="fie-overlay">
      <div ref={box} className="fie-dialog" role="dialog" aria-modal="true" aria-labelledby="fie-title" tabIndex={-1}>
        <header className="fie-head">
          <h2 id="fie-title">Edit image <span className="muted">{visual.name}</span></h2>
          <button className="icon-btn" onClick={cancel} aria-label="Close image editor" title="Close"><Icon name="tb-x" size={16} /></button>
        </header>
        <div className="fie-body">
          <EditorBoundary>
            <FilerobotImageEditor
              source={visual.url}
              theme={theme}
              tabsIds={[TABS.ADJUST, TABS.RESIZE, TABS.ANNOTATE, TABS.WATERMARK, TABS.FILTERS, TABS.FINETUNE]}
              defaultTabId={TABS.ADJUST}
              defaultToolId={TOOLS.CROP}
              removeSaveButton
              useBackendTranslations={false}
              observePluginContainerSize
              getCurrentImgDataFnRef={getImg}
              onModify={() => setDirty(true)}
              savingPixelRatio={4}
              previewPixelRatio={window.devicePixelRatio || 1}
            />
          </EditorBoundary>
        </div>
        <footer className="fie-foot">
          {error && <p className="fie-error" role="alert">{error}</p>}
          {confirm
            ? <>
              <p className="fie-confirm" role="alert">Discard your edits to {visual.name}? The original is unchanged either way.</p>
              <button className="btn btn-secondary" onClick={() => setConfirm(false)} autoFocus>Keep editing</button>
              <button className="btn btn-danger" onClick={onDone}>Discard edits</button>
            </>
            : <>
              <p className="muted xs fie-note">Save adds an edited copy to Visuals in this tab. The original stays as it is.</p>
              <button className="btn btn-secondary" onClick={cancel}>Cancel</button>
              <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
                <Icon name={saving ? 'tb-loader' : 'tb-device-floppy'} spin={saving} />{saving ? 'Saving…' : 'Save as copy'}
              </button>
            </>}
        </footer>
      </div>
    </div>,
    document.body,
  )
}
