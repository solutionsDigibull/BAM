// Demo vs real data. The mock adapter simulates the whole run, so every result in this build is a demo result.
// ponytail: one flag for the run; flip it in the HTTP adapter when real services answer.
export const DEMO_RUN = true

/** Simulated-failure controls are for demos and tests only: shown with ?demo=1 (kept for the browser session). */
export function demoControls(): boolean {
  try {
    const on = new URLSearchParams(location.search).get('demo')
    if (on !== null) sessionStorage.setItem('bam-demo-controls', on === '0' ? '0' : '1')
    return sessionStorage.getItem('bam-demo-controls') === '1'
  } catch { return false }
}
