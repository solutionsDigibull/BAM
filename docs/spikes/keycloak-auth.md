# Spike E0.13: Keycloak Organizations + Auth.js + FastAPI JWT

Issue [#31](https://github.com/solutionsDigibull/BAM/issues/31) · ADR [0002](../adr/0002-keycloak-tenancy.md) · Prototype: [`spikes/identity/`](../../spikes/identity/README.md) + `client/src/auth.ts`

## Question

Can one Keycloak realm with Organizations carry the tenant in the access token, can the Next.js client sign in per
portal with Auth.js while keeping tokens out of the browser, and can FastAPI validate those tokens strictly (alg,
issuer, audience, expiry, tenant) through JWKS, including key rotation and Keycloak downtime?

**Verdict: GO** with one realm + Organizations (O-05 default). All eight test cases pass.

## Setup

| Item | Value |
|---|---|
| Keycloak | `quay.io/keycloak/keycloak:26.8.0`, `start-dev --import-realm`, H2, on `localhost:8180` ([compose](../../spikes/identity/docker-compose.yml)) |
| Realm | [`bullm-realm.json`](../../spikes/identity/realm/bullm-realm.json): orgs `tenant-a`, `tenant-b`; clients `sbam-admin`, `sbam-fde` (public, PKCE S256), `spike-other` (no API audience); users `fde-a`, `fde-b`, `fde-ab` (both orgs), `admin-a`, `root` (super-admin) |
| API | FastAPI + PyJWT 2.15 (`PyJWKClient`), [`spikes/identity/api`](../../spikes/identity/api/), `localhost:8100` |
| Web | the real client (`client/`), Next.js 16 + Auth.js `next-auth@5.0.0-beta.32`, enabled by `KEYCLOAK_ISSUER` |

## Results by test case

| ID | Result | Evidence and method |
|---|---|---|
| `E0.13-T01` Login | **Pass** | Browser: `/login-fde` → Keycloak → `fde-a` lands on `/projects` (FDE shell). `/api/proxy/me` → `200 {"tenant_id":"tenant-a","roles":["sbam-fde"],"client_id":"sbam-fde"}`. Same for `admin-a` via `/login-admin` → `/admin/projects`. Automated: `test_e0_13_t01_me_returns_tenant_and_role` |
| `E0.13-T02` Expired | **Pass** | `exp` 5 min ago → 401 problem+json; 30 s ago (inside leeway) → 200 (`test_e0_13_t02_*`) |
| `E0.13-T03` alg none / HS256 | **Pass** | Forged `alg: none` and HS256 signed with the RSA public key PEM → 401 both (`test_e0_13_t03_*`); tampered signature → 401 |
| `E0.13-T04` Cross-tenant | **Pass** | Tenant A token on `/tenants-demo/tenant-b` → 404 (pytest, live Keycloak token, and in the browser through the proxy) |
| `E0.13-T05` JWKS rotation | **Pass** | Live: added an `rsa-generated` key (priority 500) through the admin API while the API ran. New token `kid` changed; `/me` → 200 without restart; old token still 200. Automated: `test_e0_13_t05_key_rotation_without_restart` (exactly one refetch) |
| `E0.13-T06` Browser storage | **Pass** | After login: `localStorage` and `sessionStorage` empty, `document.cookie` empty (session cookie is httpOnly), `/api/auth/session` returns only `user`, `portal`, `roles`; no JWT pattern anywhere |
| `E0.13-T07` Wrong audience | **Pass** | Live token from `spike-other` (no `sbam-api` audience) → 401; pytest with `aud` other/missing → 401 |
| `E0.13-T08` Keycloak down | **Pass** | Stopped Keycloak: existing session `/api/proxy/me` → 200 (cached JWKS). New login → login card shows "The sign-in service is unavailable" (`?error=Configuration`). Automated: `test_e0_13_t08_*` (cached key 200, unseen key 401) |

### Safety checks

- [x] Tokens kept server-side in the Auth.js session; never in localStorage/sessionStorage (T06)
- [x] JWT alg allow-list RS256 only; `none` and HS256 rejected (T03)
- [x] Cross-tenant resource → 404, not 403 (T04)
- [x] Admin client cannot be used to log into the FDE portal: `admin-a` on `/login-fde` → "This account cannot use this portal" (`?error=WrongPortal`); `fde-a` on `/login-admin` refused the same way

### Edge cases

| Case | Observed |
|---|---|
| User in two orgs | Default scope: **no** `tenant_id` claim → no tenant, tenant routes 404. With `scope=organization:tenant-b`: `["tenant-b"]` |
| Non-member asks for an org | `organization:tenant-b` for `fde-a` → claim absent |
| Token without tenant (super-admin) | `/me` 200 with `tenant_id: null`; tenant routes 404 |
| JWKS rotation while running | see T05 |

## Findings worth knowing

- The client scope **must be named `organization`** for `organization:<alias>` selection to work; with any other name
  multi-org users simply get no claim.
- With Organizations on, the login is **identity-first** (username, then password). Affects E2E scripts.
- Auth.js handles Keycloak being unreachable itself and redirects to `pages.error` with `error=Configuration`; no
  try/catch is needed in the sign-in action.
- Auth.js v5 public client: `clientSecret: ""` plus `client.token_endpoint_auth_method: "none"` and
  `checks: ["pkce", "state"]`.
- Run the web app on the same issuer host as the browser (`localhost:8180`). In Compose (#47/#51) set the Keycloak
  hostname so the browser and the containers agree on `iss`.

## Recommendation

Adopt ADR 0002. #47 turns the spike realm into the Compose realm export, #48 ports `auth.py` into `sbam_api.core`
(add an unknown-`kid` cooldown), #51 finishes the client sign-in (refresh, RP-initiated logout, org picker), #45 extends
the proxy to all methods.
