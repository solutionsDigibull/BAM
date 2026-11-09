import { cloneElement, createContext, useContext, useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type ReactElement, type ReactNode } from 'react'
import { IconPlayerPlay, IconRobot, IconDots, IconAlertCircle, IconFolder, IconFiles, IconFileZip, IconMicrophone, IconMicrophoneOff, IconPlayerStop, IconLoader, IconInbox, IconGitBranch, IconBriefcase, IconShieldCheck, IconUpload, IconTransform, IconFileCode, IconClipboardCheck, IconRefresh, IconSearch, IconSchema, IconTarget, IconTable, IconTemplate, IconCode, IconBox, IconListCheck, IconCertificate, IconDatabaseImport, IconPhoto, IconPencil, IconTrash, IconX, IconDeviceFloppy, IconCopy, IconCheck, IconThumbUp, IconThumbDown, IconPlus, IconPlayerStopFilled, IconFileText, IconSend, IconArrowRight, IconAlertTriangle, IconFile, IconChevronDown, IconArrowDown, type TablerIcon } from '@tabler/icons-react'
import { setUi, useUi } from '../lib/store'
import type { RunStatus, Stage, StepState } from '../api/types'
import { BAM_ACTIONS, SUCCESS_MS, actionView, isNewSuccess, type BamAction } from '../lib/bamUi'
import bullLogo from '../assets/login/bull-head-transparent.png'

// ---- icons (inline, 16px, currentColor) --------------------------------------
const paths: Record<string, ReactNode> = {
  check: <path d="M3.5 8.5l3 3 6-7" />,
  x: <path d="M4 4l8 8M12 4l-8 8" />,
  shield: <><path d="M8 1.8l5 2v4c0 3-2.2 5.2-5 6.4-2.8-1.2-5-3.4-5-6.4v-4z" /><path d="M5.8 8l1.6 1.6 2.9-3.2" /></>,
  lock: <><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" /><path d="M5.5 7V5a2.5 2.5 0 015 0v2" /></>,
  dot: <circle cx="8" cy="8" r="3" fill="currentColor" />,
  alert: <><path d="M8 2.5l6 10.5H2z" /><path d="M8 6.5v3M8 11.2v.3" /></>,
  clock: <><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3l2 1.5" /></>,
  minus: <path d="M4 8h8" />,
  circle: <circle cx="8" cy="8" r="5" />,
  half: <><circle cx="8" cy="8" r="5" /><path d="M8 3a5 5 0 010 10z" fill="currentColor" /></>,
  eye: <><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="2" /></>,
  question: <><circle cx="8" cy="8" r="5.5" /><path d="M6.5 6.5a1.5 1.5 0 113 0c0 1-1.5 1.2-1.5 2.3M8 11v.3" /></>,
  spinner: <path d="M8 2.5a5.5 5.5 0 105.5 5.5" />,
  upload: <><path d="M8 11V3M4.5 6.5L8 3l3.5 3.5" /><path d="M3 11.5V13h10v-1.5" /></>,
  sun: <><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" /></>,
  moon: <path d="M13 9.5A5.5 5.5 0 016.5 3a5.5 5.5 0 106.5 6.5z" />,
  chevron: <path d="M6 4l4 4-4 4" />,
  back: <path d="M10 4L6 8l4 4" />,
  down: <path d="M4 6l4 4 4-4" />,
  file: <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3" /></>,
  link: <><path d="M7 9a2.5 2.5 0 003.5 0l2-2A2.5 2.5 0 009 3.5l-.5.5" /><path d="M9 7a2.5 2.5 0 00-3.5 0l-2 2A2.5 2.5 0 007 12.5l.5-.5" /></>,
  refresh: <><path d="M12.5 6A5 5 0 003.6 5M3.5 10a5 5 0 008.9 1" /><path d="M3.5 2.5V5H6M12.5 13.5V11H10" /></>,
  expand: <path d="M3 6V3h3M13 6V3h-3M3 10v3h3M13 10v3h-3" />,
  edit: <path d="M10.5 3l2.5 2.5L6 12.5H3.5V10z" />,
  db: <><ellipse cx="8" cy="4" rx="5" ry="2" /><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" /></>,
  bolt: <path d="M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z" />,
  copy: <><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" /><path d="M10.5 5.5V3.5a1 1 0 00-1-1h-6a1 1 0 00-1 1v6a1 1 0 001 1h2" /></>,
  ingest: <><path d="M8 2.5v7.5M4.8 7L8 10.2 11.2 7" /><path d="M3 13h10" /></>,
  sparkle: <path d="M8 2l1.4 4.6L14 8l-4.6 1.4L8 14l-1.4-4.6L2 8l4.6-1.4z" />,
  rocket: <><path d="M14 2L2 7.2l4.6 2.2L8.8 14z" /><path d="M6.6 9.4L14 2" /></>,
  doccheck: <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3M6 10l1.5 1.5L10.5 8" /></>,
  save: <><path d="M3 3h8l2 2v8H3z" /><path d="M5.5 3v3h5V3M5.5 13v-3.5h5V13" /></>,
  palette: <><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="9" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="2.5" y="9" width="4.5" height="4.5" rx="1" /><rect x="9" y="9" width="4.5" height="4.5" rx="1" /></>,
  // Tabler-style outline icons (local SVG, no icon package): v3 navigation, top bar and Enterprise Assistant.
  'cloud-up': <><path d="M4.5 12H4a2.8 2.8 0 01-.4-5.6A4 4 0 0111.4 5 3.2 3.2 0 0112 12h-.5" /><path d="M8 13.5V8M6 10l2-2 2 2" /></>,
  scan: <><path d="M2.5 5V3.5a1 1 0 011-1H5M11 2.5h1.5a1 1 0 011 1V5M13.5 11v1.5a1 1 0 01-1 1H11M5 13.5H3.5a1 1 0 01-1-1V11" /><path d="M2.5 8h11" /></>,
  clipboard: <><path d="M6 2.5h4v2H6z" /><path d="M10 3.5h1.5a1 1 0 011 1V13a1 1 0 01-1 1h-7a1 1 0 01-1-1V4.5a1 1 0 011-1H6" /><path d="M6 8.5l1.3 1.3L10 7" /></>,
  plug: <><path d="M5.5 2.5v3M10.5 2.5v3" /><path d="M4 5.5h8v2a4 4 0 01-8 0z" /><path d="M8 11.5v2.5" /></>,
  bug: <><rect x="5" y="5" width="6" height="8" rx="3" /><path d="M6 5V4a2 2 0 014 0v1M2.5 9H5M11 9h2.5M3 6l2 1M13 6l-2 1M3 12.5l2-1.5M13 12.5l-2-1.5M8 7.5V13" /></>,
  graph: <><circle cx="4" cy="4" r="1.6" /><circle cx="12" cy="5" r="1.6" /><circle cx="6" cy="12" r="1.6" /><path d="M5.5 4.3l5 .5M4.6 5.5l1 5M10.9 6.2L7.2 10.9" /></>,
  schema: <><rect x="2.5" y="2.5" width="11" height="11" rx="1.5" /><path d="M2.5 6h11M6.5 6v7.5" /></>,
  gauge: <><path d="M2.6 11.5a6 6 0 1110.8 0" /><path d="M8 9.5l2.5-3" /><circle cx="8" cy="9.8" r=".9" /></>,
  briefcase: <><rect x="2" y="5" width="12" height="8.5" rx="1.5" /><path d="M5.5 5V3.8a1.3 1.3 0 011.3-1.3h2.4a1.3 1.3 0 011.3 1.3V5M2 9h12" /></>,
  'file-text': <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3M6 8h4M6 10.5h4" /></>,
  grid: <><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="9" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="2.5" y="9" width="4.5" height="4.5" rx="1" /><rect x="9" y="9" width="4.5" height="4.5" rx="1" /></>,
  'circle-check': <><circle cx="8" cy="8" r="5.8" /><path d="M5.6 8.2l1.6 1.6 3.2-3.4" /></>,
  braces: <><path d="M5.5 2.5H5a1.5 1.5 0 00-1.5 1.5v2.2L2.5 8l1 1.8V12A1.5 1.5 0 005 13.5h.5M10.5 2.5h.5A1.5 1.5 0 0112.5 4v2.2l1 1.8-1 1.8V12a1.5 1.5 0 01-1.5 1.5h-.5" /></>,
  code: <path d="M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5M9 3L7 13" />,
  checklist: <><path d="M2.5 4l1 1 2-2M2.5 8.5l1 1 2-2M2.5 13l1 1 2-2" /><path d="M7.5 4.5h6M7.5 9h6M7.5 13.5h6" /></>,
  flask: <><path d="M6 2.5h4M6.5 2.5v4L3 12.6a1 1 0 00.9 1.4h8.2a1 1 0 00.9-1.4L9.5 6.5v-4" /><path d="M4.5 10h7" /></>,
  award: <><circle cx="8" cy="6" r="3.8" /><path d="M5.8 9.2L5 14l3-1.6 3 1.6-.8-4.8" /></>,
  package: <><path d="M8 1.8l5.5 3v6.4L8 14.2l-5.5-3V4.8z" /><path d="M2.5 4.8L8 7.8l5.5-3M8 7.8v6.4M5.2 3.3l5.6 3" /></>,
  user: <><circle cx="8" cy="5.5" r="2.8" /><path d="M2.8 14a5.2 5.2 0 0110.4 0" /></>,
  search: <><circle cx="7" cy="7" r="4.5" /><path d="M10.4 10.4L14 14" /></>,
  send: <><path d="M14 2L7 9M14 2l-4.5 12L7 9 2 6.5z" /></>,
  paperclip: <path d="M10.5 4.5L5.6 9.4a1.3 1.3 0 001.8 1.8l5-5a2.6 2.6 0 00-3.7-3.7l-5 5a3.9 3.9 0 005.5 5.5l4.3-4.3" />,
  layout: <><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="9" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="2.5" y="9" width="4.5" height="4.5" rx="1" /><rect x="9" y="9" width="4.5" height="4.5" rx="1" /></>,
  table: <><rect x="2.5" y="2.5" width="11" height="11" rx="1.5" /><path d="M2.5 6.5h11M6.5 2.5v11" /></>,
  menu: <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />,
  logout: <><path d="M9.5 5V3.5a1 1 0 00-1-1h-5a1 1 0 00-1 1v9a1 1 0 001 1h5a1 1 0 001-1V11" /><path d="M6.5 8h7.5M11.5 5.5L14 8l-2.5 2.5" /></>,
  'panel-left': <><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M6 2.5v11" /></>,
  'panel-right': <><rect x="2" y="2.5" width="12" height="11" rx="1.5" /><path d="M10 2.5v11" /></>,
  note: <><path d="M3 2.5h10v8l-3 3H3z" /><path d="M10 13.5v-3h3M5.5 6h5M5.5 8.5h3" /></>,
  plus: <path d="M8 3.5v9M3.5 8h9" />,
  info: <><circle cx="8" cy="8" r="5.8" /><path d="M8 7.2v3.8M8 5v.2" /></>,
  mic: <><rect x="6" y="2" width="4" height="7.5" rx="2" /><path d="M3.8 7.5a4.2 4.2 0 008.4 0M8 11.7V14" /></>,
  play: <path d="M5 3.5v9l7.5-4.5z" />,
  folder: <path d="M2.5 4.5a1 1 0 011-1h3l1.5 1.5h4.5a1 1 0 011 1v6a1 1 0 01-1 1h-9a1 1 0 01-1-1z" />,
  zip: <><path d="M4 2h5l3 3v9H4z" /><path d="M9 2v3h3M7 4h1M7 6h1M7 8h1" /><rect x="6.5" y="9.5" width="2" height="2.5" rx=".5" /></>,
  download: <><path d="M8 3v7.5M4.8 7.5L8 10.7l3.2-3.2" /><path d="M3 12.5V13h10v-.5" /></>,
  gear: <><circle cx="8" cy="8" r="2.5" /><path d="M13.2 9.5a1.2 1.2 0 00.2 1.3l.1.1a1.2 1.2 0 01-1.7 1.7l-.1-.1a1.2 1.2 0 00-1.3-.2 1.2 1.2 0 00-.7 1.1V14a1.2 1.2 0 01-2.4 0v-.6a1.2 1.2 0 00-.8-1.1 1.2 1.2 0 00-1.3.2l-.1.1a1.2 1.2 0 01-1.7-1.7l.1-.1a1.2 1.2 0 00.2-1.3 1.2 1.2 0 00-1.1-.7H2a1.2 1.2 0 010-2.4h.6a1.2 1.2 0 001.1-.8 1.2 1.2 0 00-.2-1.3l-.1-.1a1.2 1.2 0 011.7-1.7l.1.1a1.2 1.2 0 001.3.2 1.2 1.2 0 00.7-1.1V2a1.2 1.2 0 012.4 0v.6a1.2 1.2 0 00.8 1.1 1.2 1.2 0 001.3-.2l.1-.1a1.2 1.2 0 011.7 1.7l-.1.1a1.2 1.2 0 00-.2 1.3 1.2 1.2 0 001.1.7H14a1.2 1.2 0 010 2.4h-.6a1.2 1.2 0 00-1.1.8z" /></>,
}

// Workflow stage and step icons (15 Oct 2026, BuLLM icon map): Tabler outline icons from @tabler/icons-react
// (MIT, tabler.io), 24×24 grid, stroke 2, currentColor, under a `tb-` prefix in the one shared registry.
const tabler: Record<string, TablerIcon> = {
  'tb-player-play': IconPlayerPlay,
  'tb-robot': IconRobot,
  'tb-dots': IconDots,
  'tb-alert-circle': IconAlertCircle,
  'tb-folder': IconFolder,
  'tb-files': IconFiles,
  'tb-file-zip': IconFileZip,
  'tb-microphone': IconMicrophone,
  'tb-microphone-off': IconMicrophoneOff,
  'tb-player-stop': IconPlayerStop,
  'tb-loader': IconLoader,
  'tb-inbox': IconInbox,
  'tb-git-branch': IconGitBranch,
  'tb-briefcase': IconBriefcase,
  'tb-shield-check': IconShieldCheck,
  'tb-upload': IconUpload,
  'tb-transform': IconTransform,
  'tb-file-code': IconFileCode,
  'tb-clipboard-check': IconClipboardCheck,
  'tb-refresh': IconRefresh,
  'tb-search': IconSearch,
  'tb-schema': IconSchema,
  'tb-target': IconTarget,
  'tb-table': IconTable,
  'tb-template': IconTemplate,
  'tb-code': IconCode,
  'tb-box': IconBox,
  'tb-list-check': IconListCheck,
  'tb-certificate': IconCertificate,
  'tb-database-import': IconDatabaseImport,
  'tb-photo': IconPhoto,
  'tb-pencil': IconPencil,
  'tb-trash': IconTrash,
  'tb-x': IconX,
  'tb-device-floppy': IconDeviceFloppy,
  'tb-copy': IconCopy,
  'tb-check': IconCheck,
  'tb-thumb-up': IconThumbUp,
  'tb-thumb-down': IconThumbDown,
  'tb-plus': IconPlus,
  'tb-arrow-right': IconArrowRight,
  'tb-alert-triangle': IconAlertTriangle,
  'tb-file': IconFile,
  'tb-chevron-down': IconChevronDown,
  'tb-arrow-down': IconArrowDown,
  'tb-player-stop-filled': IconPlayerStopFilled,
  'tb-file-text': IconFileText,
  'tb-send': IconSend,
}

export function Icon({ name, size = 16, spin, label, stroke }: { name: keyof typeof paths | string; size?: number; spin?: boolean; label?: string; stroke?: number }) {
  const Tb = tabler[name]
  const a11y = { 'aria-hidden': label ? undefined : true, role: label ? 'img' : undefined, 'aria-label': label }
  if (Tb) return <Tb className={spin ? 'icon spin' : 'icon'} size={size} stroke={stroke ?? 2} {...a11y} />
  return (
    <svg className={spin ? 'icon spin' : 'icon'} width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth={stroke ?? 1.6} strokeLinecap="round" strokeLinejoin="round" {...a11y}>
      {paths[name]}
    </svg>
  )
}

// ---- status: always icon + text, never colour alone -------------------------
type AnyStatus = RunStatus | StepState | 'pass' | 'fail' | 'stale' | 'pending' | 'unavailable'
const STATUS: Record<AnyStatus, { icon: string; text: string; tone: string; spin?: boolean }> = {
  unknown: { icon: 'question', text: 'Unknown', tone: 'muted' },
  queued: { icon: 'clock', text: 'Queued', tone: 'muted' },
  running: { icon: 'spinner', text: 'Running', tone: 'info', spin: true },
  succeeded: { icon: 'check', text: 'Succeeded', tone: 'ok' },
  failed: { icon: 'x', text: 'Failed', tone: 'err' },
  skipped: { icon: 'minus', text: 'Not triggered', tone: 'muted' },
  locked: { icon: 'lock', text: 'Locked', tone: 'muted' },
  active: { icon: 'dot', text: 'Active', tone: 'info' },
  needs_review: { icon: 'alert', text: 'Needs review', tone: 'warn' },
  done: { icon: 'check', text: 'Done', tone: 'ok' },
  pass: { icon: 'check', text: 'Pass', tone: 'ok' },
  fail: { icon: 'x', text: 'Fail', tone: 'err' },
  stale: { icon: 'refresh', text: 'Stale', tone: 'warn' },
  pending: { icon: 'clock', text: 'Pending', tone: 'muted' },
  unavailable: { icon: 'minus', text: 'Unavailable', tone: 'muted' },
}

export function StatusPill({ status, text }: { status: AnyStatus; text?: string }) {
  const s = STATUS[status]
  return <span className={`pill tone-${s.tone}`}><Icon name={s.icon} size={12} spin={s.spin} />{text ?? s.text}</span>
}

// ---- formatting ---------------------------------------------------------------
/** The viewer's timezone. Stored values are ISO/UTC; never offset by hand. */
export const APP_TIMEZONE: string = Intl.DateTimeFormat().resolvedOptions().timeZone
const sameDay = (a: Date, b: Date) => a.toLocaleDateString('en-CA', { timeZone: APP_TIMEZONE }) === b.toLocaleDateString('en-CA', { timeZone: APP_TIMEZONE })
/** Short local time ("11:24 AM"; earlier days add the date). Full value: `fmtFull` / `<Time>`. */
export const fmtTime = (iso?: string) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return sameDay(d, new Date())
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: APP_TIMEZONE })
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: APP_TIMEZONE })
}
export const fmtFull = (iso?: string) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'long', timeZone: APP_TIMEZONE }) : '—'

/** A timestamp: short text, full date/time and timezone in the tooltip. */
export function Time({ iso, prefix }: { iso?: string; prefix?: string }) {
  if (!iso) return <span>—</span>
  return <>{prefix}<time className="time" dateTime={iso} title={`${fmtFull(iso)} (${APP_TIMEZONE})`}>{fmtTime(iso)}</time></>
}

/** A long value (path, hash, URI) shown ellipsized, with the full value in a tooltip and a copy button that copies the original. */
export function Copy({ value, display, label = 'value', className = '' }: { value: string; display?: string; label?: string; className?: string }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(value) }
    catch (e) { console.error(`Could not copy the ${label}:`, (e as Error).message || 'clipboard unavailable') }
  }
  return (
    <span className={`copyable ${className}`}>
      <span className="copy-text mono" title={value}>{display ?? value}</span>
      <button type="button" className="copy-btn" onClick={e => { e.stopPropagation(); void copy() }} aria-label={`Copy ${label} ${value}`} title={`Copy full ${label}`}><Icon name="copy" size={12} /></button>
    </span>
  )
}
export function fmtDuration(start?: string, end?: string) {
  if (!start || !end) return undefined
  const ms = new Date(end).getTime() - new Date(start).getTime()
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}
export const fmtBytes = (n: number) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`
export const shortHash = (h: string) => `${h.slice(0, 12)}…`

// ---- layout -------------------------------------------------------------------
/** True inside the 3-pane workbench grid, where the side panes can collapse. */
export const PanesContext = createContext(false)

export function Pane({ title, kicker, actions, footer, children, className, id }: {
  title: ReactNode; kicker?: ReactNode; actions?: ReactNode; footer?: ReactNode; children: ReactNode; className?: string; id?: string
}) {
  const inPanes = useContext(PanesContext)
  const side = !inPanes ? null : id === 'left-title' ? 'left' : id === 'right-title' ? 'right' : null
  const collapsed = useUi(s => (side ? s.collapsed[side] : false))
  const bodyId = useId()
  const box = useRef<HTMLElement>(null)
  const refocus = useRef(false)
  const toggle = () => { if (!side) return; refocus.current = true; setUi(s => ({ collapsed: { ...s.collapsed, [side]: !s.collapsed[side] } })) }
  // The toggle control is swapped on collapse/expand; keep keyboard focus on its counterpart.
  useEffect(() => {
    if (!refocus.current) return
    refocus.current = false
    box.current?.querySelector<HTMLElement>(collapsed ? '.pane-rail' : '.pane-collapse')?.focus()
  }, [collapsed])
  const label = typeof title === 'string' ? title : 'pane'
  return (
    <section ref={box} className={`pane ${className ?? ''} ${collapsed ? 'collapsed' : ''}`} aria-labelledby={id} data-side={side ?? undefined}>
      {collapsed ? (
        <button className="pane-rail" onClick={toggle} aria-expanded={false} aria-controls={bodyId} aria-label={`Expand ${label} pane`} title={`Expand ${label}`}>
          <Icon name={side === 'left' ? 'chevron' : 'back'} size={14} />
          <span className="rail-title" id={id}>{title}</span>
        </button>
      ) : (
        <header className="pane-head">
          <div>
            {kicker && <div className="kicker">{kicker}</div>}
            <h2 id={id} tabIndex={-1}>{title}</h2>
          </div>
          <div className="pane-actions">
            {actions}
            {side && <button className="icon-btn sm pane-collapse" onClick={toggle} aria-expanded aria-controls={bodyId}
              aria-label={`Collapse ${label} pane`} title={`Collapse ${label}`}><Icon name={side === 'left' ? 'back' : 'chevron'} size={14} /></button>}
          </div>
        </header>
      )}
      {/* Collapsed panes stay mounted (hidden) so in-progress work such as unsaved code drafts survives. */}
      <div className="pane-body" id={bodyId} hidden={collapsed}>{children}</div>
      {footer && !collapsed && <footer className="pane-foot">{footer}</footer>}
    </section>
  )
}

/** One compact summary card (Ingestion screens): icon, title, one line of live counts, optional link row. */
export function OverviewCard({ icon, title, line, tone, link }: {
  icon: string; title: string; line: ReactNode; tone?: 'warn' | 'err' | 'ok'; link?: { label: string; action: string; onClick: () => void }
}) {
  return (
    <section className={`ing-ov ${tone ? `tone-${tone}` : ''}`} aria-live="polite">
      <div className="ing-ov-main">
        <Icon name={icon} size={16} />
        <div><h3>{title}</h3><p className="muted xs">{line}</p></div>
      </div>
      {link && <div className="ing-ov-link"><span className="sm">{link.label}</span>
        <button className="link sm" onClick={link.onClick}>{link.action}<Icon name="chevron" size={12} /></button></div>}
    </section>
  )
}

/** v3 stat tiles: live values only (never fixed wireframe numbers). `tone` pairs with the label text, never colour alone. */
export function Kpis({ items, label }: { items: { value: ReactNode; label: string; tone?: 'ok' | 'warn' | 'err' }[]; label: string }) {
  return (
    <dl className="kpis" aria-label={label}>
      {items.map(k => (
        <div key={k.label} className={`kpi ${k.tone ? `kpi-${k.tone}` : ''}`}>
          <dt>{k.label}</dt><dd>{k.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Section({ title, children, aside }:{ title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="section">
      <div className="section-head"><h3>{title}</h3>{aside}</div>
      {children}
    </div>
  )
}

// ---- designed states ----------------------------------------------------------
export function Empty({ icon = 'file', title, children, action }: { icon?: string; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state-empty">
      <div className="state-icon"><Icon name={icon} size={20} /></div>
      <p className="state-title">{title}</p>
      {children && <p className="state-body">{children}</p>}
      {action}
    </div>
  )
}

export function Skeleton({ rows = 4, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => <div key={i} className="sk-line" style={{ width: `${92 - (i % 3) * 17}%` }} />)}
      <span className="sr-only">{label}</span>
    </div>
  )
}

export function ErrorState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state-error" role="alert">
      <div className="state-icon"><Icon name="alert" size={20} /></div>
      <p className="state-title">{title}</p>
      {children && <p className="state-body">{children}</p>}
      {action}
    </div>
  )
}

export function Banner({ tone, icon, children, action }: { tone: 'info' | 'warn' | 'err' | 'ok'; icon: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className={`banner tone-${tone}`} role={tone === 'err' ? 'alert' : 'status'}>
      <Icon name={icon} />
      <div className="banner-text">{children}</div>
      {action}
    </div>
  )
}

// Hover (after ~300 ms) or keyboard focus shows what the button does, above it and aligned to its right edge; blur,
// mouse leave and Escape hide it. `note` is the second line (why it is disabled, or what is running). Never takes focus.
export function Tip({ text, note, children }: { text: string; note?: string; children: ReactElement<{ 'aria-describedby'?: string }> }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const show = (ms: number) => { clearTimeout(timer.current); timer.current = window.setTimeout(() => setOpen(true), ms) }
  const hide = () => { clearTimeout(timer.current); setOpen(false) }
  useEffect(() => () => clearTimeout(timer.current), [])
  const describedBy = [children.props['aria-describedby'], id].filter(Boolean).join(' ')
  return (
    <span className="tip-wrap" onMouseEnter={() => show(300)} onMouseLeave={hide} onFocus={() => show(0)} onBlur={hide}
      onKeyDown={e => { if (e.key === 'Escape' && open) { e.stopPropagation(); hide() } }}>
      {cloneElement(children, { 'aria-describedby': describedBy })}
      <span role="tooltip" id={id} className="tip" hidden={!open}>{text}{note && <span className="tip-note">{note}</span>}</span>
    </span>
  )
}

/** A step that runs on its own (`mode: 'automated'` in trackerConfig): robot chip, "Automated" on hover/focus. */
// The BuLLM bull glyph for automated steps; the label comes from the parent chip.
export function AutomatedIcon() {
  return <img className="auto-icon" src={bullLogo.src} alt="" aria-hidden="true" draggable={false} />
}

export function AutoChip({ className = '' }: { className?: string }) {
  return <Tip text="Automated"><span className={`auto-chip ${className}`} tabIndex={0} aria-label="Automated"><AutomatedIcon /></span></Tip>
}

/** A step gated on a person (`mode: 'hitl'` in trackerConfig): Lucide person-outline chip, "Human gate" on hover/focus. */
export function HumanIcon() {
  return <svg className="auto-icon hitl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
}

export function HitlChip({ className = '' }: { className?: string }) {
  return <Tip text="Human gate"><span className={`auto-chip hitl-chip ${className}`} tabIndex={0} aria-label="Human gate"><HumanIcon /></span></Tip>
}

// A gate button: when disabled it says why, right next to it. With `tip`, it also explains itself on hover/focus.
export function GateButton({ children, reason, onClick, variant = 'primary', busy, icon, action, successKey, tip, tipReason, tipBusy }: {
  children: ReactNode; reason?: string; onClick: () => unknown; variant?: 'primary' | 'secondary' | 'danger'; busy?: boolean; icon?: string
  /** One of the six BAM actions: renders the orange BamActionButton (loading / success labels) with the same gate reason. */
  action?: BamAction; successKey?: unknown
  /** Tooltip copy (lib/actionTips.ts `tips()`): what a click does, the disabled reason override, the running text. */
  tip?: string; tipReason?: string; tipBusy?: string
}) {
  const disabled = !!reason || busy
  // A disabled gate always says why; while its own action runs, that is "Working…".
  const why = reason ?? (busy && !action ? 'Working…' : undefined)
  const id = useId()
  const btn = action ? <BamActionButton action={action} busy={busy} blocked={!!reason} successKey={successKey} onClick={onClick} aria-describedby={why ? id : undefined}>{children}</BamActionButton> : <button className={`btn btn-${variant}`} aria-disabled={disabled || undefined}
    onClick={() => { if (!disabled) onClick() }} aria-describedby={why ? id : undefined}>
    {busy ? <Icon name="spinner" spin /> : icon && <Icon name={icon} />}
    {children}
  </button>
  return (
    <span className="gate">
      {tip ? <Tip text={tip} note={busy ? tipBusy : reason ? tipReason ?? reason : undefined}>{btn}</Tip> : btn}
      {why && <span className="gate-reason" id={id}><Icon name={reason ? 'lock' : 'spinner'} size={12} spin={!reason} />{why}</span>}
    </span>
  )
}

// ---- BAM action button: orange glass pill with loading and transient success ----------------------------------
/**
 * The parent owns operation state: `busy` is the real in-flight or running state (an enqueued job stays busy until the
 * backend reports a terminal status); `successKey` is a confirmed result from the backend (e.g. the approved version,
 * the deployed hash). Success shows for ~1.8 s when that key changes, then returns to idle; it never changes saved data.
 * `blocked` is an eligibility rule (dims the button); every lock uses aria-disabled so keyboard focus is kept.
 */
export function BamActionButton({ action, children, busy, successKey, blocked, status, onClick, className = '', ...rest }: {
  action: BamAction; children: ReactNode; busy?: boolean; successKey?: unknown; blocked?: boolean; onClick: () => unknown
  /** Fully controlled display state (the dev preview); omit to derive it from busy / successKey. */
  status?: 'idle' | 'loading' | 'success'
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick' | 'children'>) {
  const a = BAM_ACTIONS[action]
  const [flash, setFlash] = useState(false)
  const [inflight, setInflight] = useState(false)
  const lock = useRef(false)
  const lastKey = useRef(successKey)
  useEffect(() => {
    const isNew = isNewSuccess(lastKey.current, successKey)
    lastKey.current = successKey
    if (!isNew) return
    setFlash(true)
    const t = setTimeout(() => setFlash(false), SUCCESS_MS)
    return () => { clearTimeout(t); setFlash(false) }
  }, [successKey])
  const v = status ? actionView({ busy: status === 'loading', flash: status === 'success', blocked }) : actionView({ busy: busy || inflight, flash, blocked })
  const click = () => {
    // The ref is set before anything is awaited, so a second click in the same tick cannot start a second run.
    if (v.locked || lock.current) return
    const r = onClick()
    if (r instanceof Promise) {
      lock.current = true; setInflight(true)
      void r.finally(() => { lock.current = false; setInflight(false) })
    }
  }
  const label = v.phase === 'loading' ? a.loading : v.phase === 'success' ? a.success : null
  return (
    <button type="button" {...rest} className={`bam-btn is-${v.phase} ${blocked ? 'is-blocked' : ''} ${className}`}
      aria-disabled={v.locked || undefined} aria-busy={v.phase === 'loading' || undefined} onClick={click}>
      <span className="bam-ico" aria-hidden>
        {v.phase === 'loading' ? <Icon name="spinner" spin /> : v.phase === 'success' ? <span className="bam-tick"><Icon name="check" size={11} /></span> : <Icon name={a.icon} />}
      </span>
      {/* All labels share one grid cell, so the pill keeps the widest width across idle / loading / success. */}
      <span className="bam-labels">
        <span className={v.phase === 'idle' ? '' : 'off'}>{children}</span>
        <span className={v.phase === 'loading' ? '' : 'off'}>{a.loading}</span>
        <span className={v.phase === 'success' ? '' : 'off'}>{a.success}</span>
      </span>
      {v.phase === 'loading' && <span className="bam-bar" aria-hidden />}
      <span className="sr-only" aria-live="polite">{label}</span>
    </button>
  )
}

// ---- stage list: status, time, duration and run ID for every backend stage ----
export function StageList({ stages }: { stages: Stage[] }) {
  return (
    <ol className="stages">
      {stages.map((s, i) => (
        <li key={s.key} className={`stage st-${s.status}`}>
          <div className="stage-rail"><span className="stage-node">{i + 1}</span></div>
          <div className="stage-main">
            <div className="stage-top">
              <span className="stage-label">{s.label}</span>
              <StatusPill status={s.status} />
            </div>
            {s.detail && <p className="stage-detail">{s.detail}</p>}
            {s.error && <p className="stage-error"><Icon name="alert" size={12} /> {s.error}</p>}
            <dl className="meta">
              <div><dt>Started</dt><dd>{fmtTime(s.startedAt)}</dd></div>
              <div><dt>Duration</dt><dd>{fmtDuration(s.startedAt, s.endedAt) ?? (s.status === 'running' ? 'in progress' : '—')}</dd></div>
              <div><dt>Run ID</dt><dd className="mono">{s.runId ?? 'unknown'}</dd></div>
            </dl>
          </div>
        </li>
      ))}
    </ol>
  )
}

// ---- modal dialog: native <dialog>, focus returns to the trigger --------------
export function Dialog({ open, title, onClose, children, footer, className = 'dialog' }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; footer: ReactNode; className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const trigger = useRef<Element | null>(null)
  useEffect(() => {
    const d = ref.current!
    if (open && !d.open) { trigger.current = document.activeElement; d.showModal() }
    if (!open && d.open) d.close()
    // Return focus to the trigger; if the action removed it, land on the step's heading instead.
    if (!open && trigger.current) {
      const t = trigger.current as HTMLElement
      ;(t.isConnected ? t : document.getElementById('center-title'))?.focus()
      trigger.current = null
    }
  }, [open])
  return (
    <dialog ref={ref} className={className} onClose={onClose} aria-labelledby="dlg-title">
      <h2 id="dlg-title">{title}</h2>
      <div className="dialog-body">{children}</div>
      <div className="dialog-foot">{footer}</div>
    </dialog>
  )
}

// Confirm with explicit consequences. Used before every irreversible-feeling action.
export function Confirm({ open, title, consequences, confirmLabel, onConfirm, onClose, children }: {
  open: boolean; title: string; consequences: ReactNode[]; confirmLabel: string; onConfirm: () => void; onClose: () => void; children?: ReactNode
}) {
  return (
    <Dialog open={open} title={title} onClose={onClose} footer={<>
      <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary" onClick={onConfirm} autoFocus>{confirmLabel}</button>
    </>}>
      <p className="muted">What happens when you continue:</p>
      <ul className="consequences">{consequences.map((c, i) => <li key={i}><Icon name="chevron" size={12} />{c}</li>)}</ul>
      {children}
    </Dialog>
  )
}

// ---- numbered action steps (a footer of gated decisions, in order) -------------------------------
export type ActionState = 'ready' | 'locked' | 'running' | 'done' | 'failed'
export interface ActionStep {
  key: string
  caption: string
  state: ActionState
  stateText: string
  label: string
  icon: string
  onClick: () => void
  /** Why the button is unavailable; shown with a lock next to the step. */
  reason?: string
  /** What the step does, or what happened, when nothing blocks it. */
  hint?: string
  busy?: boolean
  /** A done step that can be run again (e.g. re-run tests). */
  repeatable?: boolean
  /** Renders the step as the shared BAM action button; `successKey` is its confirmed result. */
  action?: BamAction
  successKey?: unknown
}

export function ActionSteps({ steps, label }: { steps: ActionStep[]; label: string }) {
  const ids = useId()
  return (
    <ol className="action-steps" aria-label={label}>
      {steps.map((s, i) => {
        const noteId = `${ids}-${s.key}`
        const disabled = !!s.reason || !!s.busy || s.state === 'locked' || s.state === 'running' || (s.state === 'done' && !s.repeatable)
        const primary = s.state === 'ready' || (s.state === 'failed' && !s.reason)
        return (
          <li key={s.key} className={`as as-${s.state}`}>
            <div className="as-head">
              <span className="as-num" aria-hidden>
                {s.state === 'done' ? <Icon name="check" size={13} /> : s.state === 'running' ? <Icon name="spinner" size={13} spin /> : s.state === 'failed' ? <Icon name="x" size={13} /> : i + 1}
              </span>
              <span className="as-state">{s.stateText}</span>
              {i < steps.length - 1 && <span className="as-line" aria-hidden />}
            </div>
            {s.action ? <BamActionButton action={s.action} className="as-btn" aria-describedby={noteId} busy={s.busy || s.state === 'running'}
                blocked={disabled && s.state !== 'running' && !s.busy} successKey={s.successKey} onClick={s.onClick}>{s.label}</BamActionButton>
              : <button className={`btn ${primary ? 'btn-primary' : 'btn-secondary'} as-btn`} aria-disabled={disabled || undefined} aria-describedby={noteId}
              onClick={() => { if (!disabled) s.onClick() }}>
              {s.busy ? <Icon name="spinner" spin /> : <Icon name={s.icon} />}{s.label}
            </button>}
            <p className="as-note" id={noteId}>{s.reason ? <><Icon name="lock" size={12} />{s.reason}</> : s.hint}</p>
            <span className="as-cap">{s.caption}</span>
          </li>
        )
      })}
    </ol>
  )
}
