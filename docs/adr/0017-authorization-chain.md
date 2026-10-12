# 0017 — Authorization chain: policies, ports and token leeway

- Status: Accepted
- Date: 2026-10-12
- Related: issue #48 (E2.2), [ADR 0002](0002-keycloak-tenancy.md), [ADR 0005](0005-identity-and-tenancy.md), [TD §16](../design/16-security-and-multi-tenancy.md)

## Context

#48 implements the chain from TD §16: token → tenant → portal role → project assignment → state guard. Two points needed a decision:

- ADR 0002 decision 6 says 60 s of clock leeway, while #48 asks for 30 s.
- The tables the chain reads (`project_members` from #50, `audit_log` from #41, and `tenants`/`users` from #49) belong to issues that come after #48.

## Decision

1. **Leeway is 30 s** for `exp`/`nbf`, following #48. This refines ADR 0002 decision 6; the rest of decision 6 stands.
2. **Every `/api/v1` route declares a policy**, as a dependency defined in `sbam_api.core.auth`. `create_app` refuses to start if any route has none.

   | Policy | Who may call |
   |---|---|
   | `admin` | `sbam-admin` or `super-admin`, within a tenant |
   | `fde` | `sbam-fde`, within a tenant |
   | `super_admin` | `super-admin` only |
   | `internal` | no user token; the route checks its own credential, such as a signed URL or hook secret |

3. **Responses:**
   - wrong role → 403 `WRONG_PORTAL`
   - no tenant, other tenant, unassigned project or malformed id → 404, identical to a missing resource
   - Keycloak unreachable and the token's key never seen → 503 `IDP_UNAVAILABLE`
   - any other token problem → 401 `UNAUTHENTICATED`
4. **Ports instead of early tables.**
   - `MembershipLookup` defaults to "no projects": FDE project routes return 404 until #50.
   - `SecurityAudit` defaults to a structured `sbam_api.security` log line until #41.
   - Both are injected through `create_app(auth=Auth(...))`.
   - Suspended tenants and the users mirror stay with #49.
5. **Two Keycloak URLs.** `KEYCLOAK_ISSUER` must equal the token `iss`, which is the browser-facing URL. `KEYCLOAK_JWKS_URL` is where the API fetches keys inside the network.

## Consequences

- #50, #41 and #49 each plug in one implementation. No route changes when they do.
- The default is to deny: until #50 lands, no FDE can reach any project.
- A new route with no policy fails CI at app start, not in production.
