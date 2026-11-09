// Upload's right pane with zero files (22 Oct 2026, right-pane cleanup, per user; replaced the React Bits Orb): one
// explainer and greyed Results / Notifications skeletons in the real table layout. Inert, no data, no shimmer.
import './upload-idle.css'

export function UploadIdle({ leaving }: { leaving?: boolean }) {
  return (
    <div className={`up-idle${leaving ? ' up-idle-leaving' : ''}`} aria-hidden={leaving || undefined}>
      <p className="up-idle-explain">Results and notifications appear here<br />once your pack is processed.</p>
      <section className="up-idle-skel" aria-hidden>
        <span className="up-idle-label">Results</span>
        <div className="up-idle-card">{[0, 1, 2, 3, 4].map(i => <div key={i} className="up-idle-row"><i /><i /><i /><i /></div>)}</div>
      </section>
      <section className="up-idle-skel" aria-hidden>
        <span className="up-idle-label">Notifications</span>
        <div className="up-idle-card"><div className="up-idle-note"><i /></div></div>
      </section>
    </div>
  )
}
