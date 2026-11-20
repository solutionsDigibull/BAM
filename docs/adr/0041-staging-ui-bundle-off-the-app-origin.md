# 0041 — The Staging UI bundle runs off the app's origin

- Status: Proposed
- Date: 2026-11-20
- Related: #916 (WG-34), #861, #748, refines [0036](0036-staging-runtime-runs-on-the-deployers-docker-host.md) decision 6, [0032](0032-staging-replaces-the-pre-commit-preview.md)

## Context

Since #861 the workbench shows a committed workflow's UI bundle in an iframe:

- `client/src/workbench/steps/Staging.tsx`: `sandbox="allow-scripts allow-same-origin allow-forms"`;
- its `src` is the app's own route `/api/staging-ui/<project>/<workflow>/`, which adds the FDE's token server-side and
  forwards the API's strict CSP (`connect-src 'self'` plus the declared hosts);
- a host script (`__host.js`) rewrites the bundle's `/mcp` calls to `/api/proxy/.../staging/mcp`, and those calls carry
  the **session cookie**.

The bundle is certified, digest-checked and visible to the assigned FDE only, but it is generated code. With
`allow-same-origin` and the same origin, it runs **with the app's privileges**:

- it can read `document.cookie` for non-HttpOnly cookies and the app's `localStorage`/`sessionStorage`
  (the workbench keeps UI state there);
- it can call **any** `/api/proxy/api/v1/...` route as the FDE (other projects' data, Commit, secrets routes), not
  only its own `staging/mcp`;
- it can reach `window.parent` and the app's DOM.

The CSP limits where the bundle's requests go, not what it does on this origin. #916 asks for a separate origin, with
token passing that shares no cookies, the CSP kept, and tests that the bundle can't read the app's storage or cookies.

## Options

### A. A dedicated origin (recommended for production)

Serve the bundle from its own site, e.g. `https://staging-ui.<domain>` (or a port in local Compose), through a small
route that holds no app session.

- **Files:** the app issues a short-lived (e.g. 5 min) **signed URL** per frame load: HMAC over
  project, workflow, version, bundle digest and expiry, like the signed file downloads of #398/#402. The bundle origin
  checks the signature and serves the digest-checked files with the existing CSP. No cookie is involved.
- **MCP calls:** the frame receives a **per-deployment, per-FDE capability token** by `postMessage` from the parent
  (targeted at the bundle origin, never `*`). The token is good only for `POST|DELETE W/staging/mcp` of that
  workflow and expires with the frame. The bundle origin's MCP route forwards it to the API, which checks it.
- **Iframe:** `sandbox="allow-scripts allow-forms allow-same-origin"` is then harmless (it is the *bundle's* origin);
  the app's CSP gets `frame-src https://staging-ui.<domain>`.
- **Needs (infrastructure):**
  - a DNS name and TLS certificate (or a port locally);
  - a Compose/proxy service or Next route on that host;
  - cookie scoping so the app's cookies are host-only, not `Domain=.<domain>`, or the subdomain receives them;
  - the HMAC key shared between the app and the bundle origin (a Docker secret).

### B. An opaque sandbox origin on the same host (no DNS)

Drop `allow-same-origin`: the frame gets a unique opaque origin and can no longer read the app's cookies, storage or
DOM.

- **What breaks today:**
  - the bundle's `<script type="module" crossorigin>` and asset requests come from origin `null`, so they need CORS
    (`Access-Control-Allow-Origin: null` is unsafe);
  - SameSite cookies are not sent on its requests, so `/api/staging-ui/...` can't authenticate them;
  - `__host.js`'s same-origin `fetch` to `/api/proxy` stops working.
- **What it would take:**
  - signed asset URLs (as in A), or inlining the bundle into `srcdoc`, which defeats the digest-checked file serving;
  - the `/mcp` calls through a `postMessage` bridge, where the parent performs only `staging/mcp` calls for this
    workflow and validates every message.

It needs no new host, but it rewrites how the bundle is served, and an opaque origin limits generated UIs
(no storage, no module workers).

### C. Keep the same origin and narrow the proxy

Keep `allow-same-origin`, and make `/api/proxy` refuse requests whose `Sec-Fetch-Dest` is `iframe`, or whose referrer
is a Staging frame, except `staging/mcp`. This is cheap but **not a boundary**: the bundle can still read the app's
storage and DOM, and headers can be steered from script. **Not recommended** except as a stop-gap.

## Proposed decision

**A** for staging and production. Until the infrastructure exists, apply the **stop-gap from C** (proxy refuses
frame-originated calls other than `staging/mcp`) so the widest risk, acting as the FDE on any route, is closed now.
The decision on A needs the answers below.

## Open questions (for the owners)

1. **Host and DNS (platform, D1):** which name serves the bundle in staging and production
   (`staging-ui.<domain>`?), and is its TLS certificate issued with the app's? Locally, is a second port on the
   `sbam-web` service, or a separate Compose service, acceptable?
2. **Cookies (D1):** are all app cookies host-only today (no `Domain=` attribute), so a subdomain never receives them?
   This must hold for A.
3. **Who signs and checks (D1/D3):** the API (`/staging/ui` signed like `/files`) or the Next app? The HMAC key is a
   Docker secret either way.
4. **Capability token for MCP (D3):** a new short-lived token type in the API, scoped to one workflow's
   `staging/mcp`, rather than forwarding the FDE's access token. Is a new token type acceptable to the identity owner?
5. **Stop-gap now?** Should the C stop-gap ship first, as a separate small PR on `/api/proxy` (D1 file)?

## Consequences (if A is accepted)

- New: a bundle-origin route or service, a signing key secret, `frame-src` in the app CSP, and a capability-token
  check on `staging/mcp`.
- Tests (#916):
  - the frame can't read `document.cookie` or `localStorage` of the app origin;
  - a call from the frame to any non-MCP proxy route is refused;
  - an expired or other-workflow signed URL gets 403;
  - the CSP is unchanged on the bundle's responses.
- 0036 decision 6 changes: the bundle is no longer served on the app's origin.
