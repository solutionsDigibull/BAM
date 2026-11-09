// Every result in the workbench comes from the SBAM backend; there is no demo mode.
export const DEMO_RUN = false

/** Simulated-failure controls do not exist. */
export function demoControls(): boolean {
  return false
}
