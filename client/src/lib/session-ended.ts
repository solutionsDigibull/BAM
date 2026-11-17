// The API proxy answered 401: the Auth.js session is over (#796). Every screen that polls would
// otherwise keep calling the API, get 401 again every few seconds, and end in a Next.js runtime
// error. One redirect to the portal's login page leaves the page, which stops all of its timers.
// The login server action re-validates `callbackUrl` (safeCallback), so it is only a hint here.

let ended = false;

export const loginPath = (): string =>
  typeof window !== "undefined" && window.location.pathname.startsWith("/admin")
    ? "/login-admin"
    : "/login-fde";

/** True once a 401 was seen: the page is on its way to login. */
export const sessionHasEnded = (): boolean => ended;

/** Redirect to login once, whatever number of requests fail with 401 at the same time. */
export function endSession(): void {
  if (ended || typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return; // already there: no redirect loop
  ended = true;
  const here = `${window.location.pathname}${window.location.search}`;
  const target = new URL(loginPath(), window.location.origin);
  if (here !== "/") target.searchParams.set("callbackUrl", here);
  // A full navigation on purpose: it drops the router, the query cache and every poll timer.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`${target.pathname}${target.search}`);
}

/** Test hook: forget a previous 401. */
export const resetSessionEnded = (): void => {
  ended = false;
};

const statusOf = (error: unknown): number | undefined => {
  const e = error as { statusCode?: number; status?: number } | null | undefined;
  return e?.statusCode ?? e?.status;
};

/**
 * Wraps a `refetchInterval` callback: once the polled query itself failed with 401 the session is
 * over, so polling stops and the user goes to login. Only polled queries do this; the shell widgets
 * (notification centre, project switcher) and writes keep their own handling.
 */
export function guardPoll<Q extends { state: { error: unknown } }, R>(
  interval: (query: Q) => R,
): (query: Q) => R | false {
  return (query) => {
    if (statusOf(query.state.error) !== 401) return interval(query);
    endSession();
    return false;
  };
}
