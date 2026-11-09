import { useSyncExternalStore } from 'react'

// The signed-in user comes from the host app (Auth.js session); this module is only the seam the shell reads.
// The workbench stores no session, password or token of its own.
interface HostSession {
  email: string | null
  signOut: () => void
}

let host: HostSession = { email: null, signOut: () => {} }
const subs = new Set<() => void>()

/** Called by the host with the current user (or null) and its sign-out action. */
export function setHostSession(next: HostSession): void {
  host = next
  subs.forEach(f => f())
}

export const useSession = (): string | null =>
  useSyncExternalStore(
    cb => {
      subs.add(cb)
      return () => {
        subs.delete(cb)
      }
    },
    () => host.email,
    () => null,
  )

export const signOut = (): void => host.signOut()
