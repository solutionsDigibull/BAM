# 0002 — Keycloak tenancy: one realm with Organizations

- Status: Accepted
- Date: 2026-10-07
- Related: issue #31 (E0.13), spike [keycloak-auth.md](../spikes/keycloak-auth.md), O-05, TD §16, TD §9; consumed by #47, #48, #49, #51, #45

## Context

SBAM is multi-tenant, and every API call must be scoped to exactly one tenant. Open item O-05 asks whether tenants are
**Organizations in one Keycloak realm** (the recommendation) or **one realm per tenant**. The spike also had to show
that the access token can carry the tenant, that the web app keeps tokens server-side, and that FastAPI validates them
safely.

## Decision

1. **One realm `bullm`; one Keycloak Organization per tenant.** `organizationsEnabled: true`. The org **alias** is the
   tenant key in the token (stable, admin-set, URL-safe). Realm-per-tenant is rejected: it multiplies clients, roles,
   mappers and admin work per tenant, and a super-admin would need a session per realm.
2. **Claim.** Client scope named **`organization`** (the name matters: Keycloak only honours `organization:<alias>`
   selection on a scope with that name) with the mapper `oidc-organization-membership-mapper`:
   `claim.name = tenant_id`, `multivalued = true`, `addOrganizationId = false`, `addOrganizationAttributes = false`,
   in the access and ID tokens. It is a **default** scope on `sbam-admin` and `sbam-fde`.
   The token carries `"tenant_id": ["<alias>"]`.
3. **Tenant rule (API).** `tenant_id` must be a list with **exactly one** alias; anything else (missing, empty, two
   aliases) means *no tenant*, and tenant-scoped routes answer **404**. Observed Keycloak behaviour that makes this safe:
   - member of one org → `["tenant-a"]`;
   - member of two orgs with no selection → claim **absent**; with scope `organization:tenant-b` → `["tenant-b"]`;
   - requesting `organization:<alias>` for an org the user is not in → claim absent;
   - super-admin (no org) → claim absent; super-admin is allowed only on non-tenant routes.
4. **Roles** are realm roles `super-admin`, `sbam-admin`, `sbam-fde`, emitted as `realm_access.roles`.
5. **Clients.** `sbam-admin` and `sbam-fde` are **public** OIDC clients with PKCE S256 (no client secret to manage).
   An audience mapper adds `sbam-api` to the access token. The web app refuses a login whose roles don't match the
   portal's client (admin roles through `sbam-admin`, `sbam-fde` through `sbam-fde`), and the portal layouts still
   check roles on every request.
6. **Token validation (FastAPI).** Algorithm allow-list `RS256` only, checked on the header **before** key lookup
   (`none` and `HS*` are refused); `iss` = realm URL; `aud` contains `sbam-api`; required `exp`, `iat`, `sub`;
   60 s leeway. Keys come from the realm JWKS, cached; an unknown `kid` triggers one refetch (rotation without
   restart). Cached keys keep verifying while Keycloak is down. All failures → 401 problem+json.
7. **Web session.** Auth.js v5, JWT session strategy (encrypted httpOnly cookie). The access token lives only inside
   that cookie; the session object exposed to pages holds `roles` and `portal` only. The browser reaches the API only
   through `/api/proxy/*`, which attaches the token server-side.

## Limits and follow-ups

- Login is identity-first when Organizations are on (username page, then password page). E2E tests (#135) must expect it.
- Two-org users need an org picker: #51 should request `organization` interactively (Keycloak shows its org selection)
  or pass `organization:<alias>`; until then such users have no tenant and see only non-tenant pages.
- Unknown-`kid` refetch is not rate-limited in the spike; #48 adds a cooldown so forged `kid`s cannot hammer Keycloak.
- No token refresh and no RP-initiated Keycloak logout in the spike; #51 adds both.
- `directAccessGrantsEnabled` is on in the spike realm for scripted tests only; the real realm (#47) turns it off.
- The org **alias** is the tenant key. Renaming an alias is a tenant migration; #49 must forbid it or map aliases to
  tenant UUIDs in the `tenants` table.

## Consequences

#47 builds the real realm export from `spikes/identity/realm/bullm-realm.json` (minus test users and direct grants).
#48 ports `spikes/identity/api/auth.py` into `sbam_api.core` behind the existing `tenant_id_var`. #51 continues from
the Auth.js wiring already in `client/src/auth.ts`.
