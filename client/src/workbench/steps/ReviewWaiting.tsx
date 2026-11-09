// Ingestion > Review before Standardize hands any file on (22 Oct 2026, review waiting-pane design, per user): the
// whole pane is one waiting state. A large React Bits Status Mark (idle = "pending") with a file icon, "Waiting for
// files" in React Bits Shiny Text, two muted lines. No counts, note, hand-off or actions. When files are handed on,
// `leaving` plays it out: the ring shrinks toward the cards' small Status Marks while the review fades in.
import ShinyText from '../components/reactbits/ShinyText'
import StatusMark from '../components/reactbits/StatusMark'
import '../components/reactbits/reactbits.css'
import { Icon } from '../components/ui'
import { useUi } from '../lib/store'
import './review-waiting.css'

// Shiny Text parses colour strings (CSS vars do not parse): text token with a soft accent sheen, per theme.
const SHINE = { light: { color: '#0F1B2D', shine: '#F2F9FD' }, dark: { color: '#E6EDF7', shine: '#5B83F2' } }

export function ReviewWaiting({ leaving }: { leaving?: boolean }) {
  const s = SHINE[useUi(u => u.theme)]
  return (
    <div className={`rw${leaving ? ' rw-leaving' : ''}`} aria-hidden={leaving || undefined}>
      <div className="rw-mark">
        <StatusMark className="rb-sm" status="pending" color="var(--accent)" size={88} strokeWidth={1.4} dashes={12} />
        <Icon name="tb-file-text" size={30} stroke={1.6} />
      </div>
      <h3 className="rw-title" role="status"><ShinyText className="rb-shiny" text="Waiting for files" color={s.color} shineColor={s.shine} speed={3.2} delay={1.2} shineWidth={30} /></h3>
      <p className="rw-sub">Upload pack files on the left.<br />Review opens once ingestion has started.</p>
    </div>
  )
}
