// Dictate states (16 Oct 2026; Requesting added 21 Oct 2026): Idle · Requesting microphone · Listening · Transcribing · Draft ready · Microphone blocked · Error. Dictation is
// the browser's speech recognition through assistant-ui (WebSpeechDictationAdapter); this wraps that adapter so the
// composer can show what it is doing. A microphone level meter (getUserMedia + an AnalyserNode) drives the waveform
// and is also how a blocked microphone is detected. The transcript lands in the composer as an editable draft; nothing
// is sent until the FDE presses send.
// ponytail: no server transcription exists in this build (no dictate endpoint); swap `inner` for a recorder +
// upload adapter when one does, keeping these states.
import { useSyncExternalStore } from 'react'
import { WebSpeechDictationAdapter, type DictationAdapter } from '@assistant-ui/react'

// requesting: getUserMedia is pending (the browser may be showing its permission prompt); the timer starts only once
// the microphone is granted and listening really begins.
export type DictatePhase = 'idle' | 'requesting' | 'listening' | 'transcribing'
export interface DictateState { phase: DictatePhase; startedAt: number; levels: number[]; blocked: boolean; error?: string }
const BARS = 16
let state: DictateState = { phase: 'idle', startedAt: 0, levels: Array(BARS).fill(0), blocked: false }
const subs = new Set<() => void>()
const set = (p: Partial<DictateState>) => { state = { ...state, ...p }; subs.forEach(f => f()) }
export const useDictate = () => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb) } }, () => state)

// A microphone the browser already denies shows as blocked before the first press.
if (typeof navigator !== 'undefined') void navigator.permissions?.query({ name: 'microphone' as PermissionName })
  .then(p => { const sync = () => set({ blocked: p.state === 'denied' }); sync(); p.onchange = sync }).catch(() => {})

let current: DictationAdapter.Session | undefined
/** Escape (and the stop button) end the listening session. */
export const stopDictation = () => { void current?.stop() }

let meter: { stream: MediaStream; ctx: AudioContext; raf: number } | undefined
function stopMeter() {
  if (!meter) return
  cancelAnimationFrame(meter.raf); meter.stream.getTracks().forEach(t => t.stop()); void meter.ctx.close(); meter = undefined
  set({ levels: Array(BARS).fill(0) })
}
async function startMeter(onBlocked: () => void) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (state.phase !== 'requesting') { stream.getTracks().forEach(t => t.stop()); return }
    set({ phase: 'listening', startedAt: Date.now(), blocked: false })
    const ctx = new AudioContext(), an = ctx.createAnalyser()
    an.fftSize = 64; ctx.createMediaStreamSource(stream).connect(an)
    const data = new Uint8Array(an.frequencyBinCount)
    let last = 0
    const tick = (t: number) => {
      if (!meter) return
      meter.raf = requestAnimationFrame(tick)
      if (t - last < 66) return // ~15 frames a second is enough for the bars
      last = t; an.getByteFrequencyData(data)
      set({ levels: Array.from({ length: BARS }, (_, i) => data[Math.min(data.length - 1, i + 1)] / 255) })
    }
    meter = { stream, ctx, raf: requestAnimationFrame(tick) }
  } catch {
    // Denied, or any other getUserMedia failure (no input device, hardware error): blocked, back to idle; a successful
    // retry clears it (above).
    if (state.phase === 'requesting') onBlocked()
  }
}

/** The adapter given to assistant-ui: the browser recognizer, with the phases above. */
export function createDictation(): DictationAdapter | undefined {
  if (typeof window === 'undefined' || !WebSpeechDictationAdapter.isSupported()) return undefined
  const inner = new WebSpeechDictationAdapter()
  return {
    listen() {
      const session = inner.listen()
      current = session
      set({ phase: 'requesting', startedAt: 0, error: undefined })
      if (!navigator.mediaDevices?.getUserMedia) set({ phase: 'listening', startedAt: Date.now() })
      else void startMeter(() => { set({ blocked: true, phase: 'idle' }); session.cancel() })
      // The browser can end the session itself (silence, an error): follow its status back to idle.
      const watch = setInterval(() => {
        const st = session.status
        if (st.type !== 'ended') return
        clearInterval(watch); stopMeter()
        if (current === session) current = undefined
        set({ phase: 'idle', error: st.reason === 'error' && !state.blocked ? 'Dictation stopped with an error. Try again, or type instead.' : undefined })
      }, 150)
      return {
        get status() { return session.status },
        // Stop while the microphone is still being requested cancels; stop while listening transcribes.
        stop: async () => {
          if (state.phase === 'requesting') { set({ phase: 'idle' }); session.cancel(); return }
          if (state.phase === 'listening') { set({ phase: 'transcribing' }); stopMeter() }
          await session.stop()
        },
        cancel: () => session.cancel(),
        onSpeechStart: cb => session.onSpeechStart(cb),
        onSpeechEnd: cb => session.onSpeechEnd(cb),
        onSpeech: cb => session.onSpeech(cb),
      }
    },
  }
}

export const fmtClock = (ms: number) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }
