# 0025 — Signing out revokes the session at the API, not only in the browser and Keycloak

- Status: Accepted
- Date: 2026-11-03
- Related: issue #458 (Audit A-32, finding F-35), [0002](0002-keycloak-tenancy.md) tenant and session claims, A-04 auth state

## Context

The API verifies the Keycloak access token offline until its `exp` (300 s). After "Sign out" (RP-initiated
logout) a token copied beforehand therefore kept returning 200 for up to five minutes: Keycloak's session was
ended, but nothing the API checks said so. The audit measured 200 at t+3 s, t+123 s and t+243 s and 401 from
t+344 s.

## Decision

1. **A server-side denylist of signed-out `sid`s**, the smallest change that makes the old token fail at once:
   table `revoked_sessions (sid, keycloak_sub, expires_at)`, not tenant data and never listed. `current_user`
   asks `is_revoked(sid)` right after the token verifies (one primary-key read) and answers 401 when it is there.
   Another session of the same user has another `sid` and is untouched.
2. **The portal tells the API.** `POST /api/v1/auth/logout` (any signed-in caller, 204, no body) records the
   caller's own `sid`; it takes no id, so nobody can revoke someone else's session. The portal's sign-out action
   calls it server-side with the session's access token just before the Keycloak end-session redirect. It is
   best effort with a 3 s timeout: signing out never fails because the API is slow, and the redirect happens
   either way.
3. **Entries expire.** A row lives one hour (`REVOCATION_TTL`, which must be at least the longest access-token
   lifespan Keycloak issues), and every sign-out sweeps the expired rows, so the table holds about an hour of
   sign-outs.

## Alternatives rejected

- *Accept and document*: leaves a five-minute window after the one action the user takes to end a session.
- *Shorter access tokens for admin roles*: narrows the window, costs a refresh round trip on every admin screen,
  and still leaves one.
- *Back-channel logout or Keycloak LOGOUT events as the source*: the right source for a sign-out from another
  device or the admin console, but the importer deliberately does not copy plain LOGOUT events (volume) and a
  delay of a poll interval would remain. Left as a follow-up; this table is where such a feed would write.

## Consequences

One extra indexed read per authenticated request. A sign-out from Keycloak's own account page or the admin console
does not call the portal, so those sessions still run until `exp`. A token without a `sid` claim cannot be revoked
and is not affected.
