# 0005 — Identity and tenancy

- Status: Accepted
- Date: 2026-10-12
- Related: issue #18 (E0.1), plan C-03, C-04, O-05, [TD §16](../design/16-security-and-multi-tenancy.md)

## Context

SBAM serves several client organisations that must never see each other's data, and needs one identity provider for both portals.

## Decision

Confirmed decisions, copied verbatim from [PLAN §3](../plan/02-confirmed-decisions.md):

| ID | Topic | Decision | Source |
|---|---|---|---|
| C-03 | Identity | Keycloak | QA B6 |
| C-04 | Tenancy | Multi-tenant; client organisations isolated | QA B7 |

### Open items

Defaults from [PLAN §30](../plan/07-risks-and-open-items.md). A default stands until the owner answers; a different answer becomes a new ADR that supersedes this one.

| ID | Item | Status | Default |
|---|---|---|---|
| O-05 | Keycloak tenancy: one realm with Organizations (recommended) vs realm per tenant | Resolved by [ADR 0002](0002-keycloak-tenancy.md) (spike #31) | One realm + Organizations |

## Consequences

- Keycloak is the identity provider. Tenants are Keycloak Organizations in one realm; [ADR 0002](0002-keycloak-tenancy.md) has the details, and its 2026-10-12 amendment makes the organization alias the tenant UUID (#47).
- Every repository query is tenant-scoped. Cross-tenant access returns 404, never 403 (TD §16, [ADR 0003](0003-sync-sqlalchemy-psycopg.md) and the repository base from #36).

## Sources

- [PLAN §3](../plan/02-confirmed-decisions.md) (source column: QA answers and follow-ups)
- [PLAN §30](../plan/07-risks-and-open-items.md)
- [TD §16](../design/16-security-and-multi-tenancy.md)
