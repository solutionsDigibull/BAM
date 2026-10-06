> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# Phase 2 — Identity, Multi-Tenancy and RBAC

| Epic | Milestone | Stories |
|---|---|---|
| [#3](https://github.com/solutionsDigibull/BAM/issues/3) `E2` | M0 Foundation | 7 |


**Goal:** Build the security boundary (Keycloak, tenant isolation and the two portals) before any client data enters the system.

**Owner:** Dev A · **Indicative effort:** 1 week · **Complexity:** M · **Depends on:** Phase 1
**Design references:** [TD §7](../design/07-database-design.md) (tenancy tables), [TD §8.2](../design/08-api-design.md), [TD §16](../design/16-security-and-multi-tenancy.md).

### Keycloak

- [ ] Realm `bullm`; clients `sbam-admin` and `sbam-fde` (OIDC, PKCE); roles `super-admin`, `sbam-admin`, `sbam-fde`; tenant membership (Organizations or group-based per O-05).
- [ ] Token mappers: `tenant_id`, roles, `sub`; short access-token lifetime; refresh rotation.
- [ ] Realm export committed for repeatable local, dev and staging setup.

### Backend authorization

- [ ] JWT validation (JWKS cache), current-user dependency, tenant resolution.
- [ ] Authorization chain:

```text
Valid Keycloak token
        ↓
Tenant (tenant_id claim must match resource)
        ↓
Portal role (sbam-admin for Admin APIs, sbam-fde for FDE APIs)
        ↓
Project access (FDE assigned to project)
        ↓
Resource state guard (e.g., approved SOW is read-only, frozen Workflow is immutable)
```

- [ ] Tenant-scoped repository base class (every query filtered by `tenant_id`); cross-tenant access returns 404.
- [ ] RBAC management APIs for Admin: invite or assign users, assign FDEs to projects.
- [ ] Audit table writes for all security-relevant actions.

### Frontend

- [ ] Separate Admin and FDE login entry points and landing pages; role-based route protection (UX only, backend authoritative).
- [ ] Admin "Users & Roles" screen.

### Tests

- [ ] RBAC matrix test (each role × each endpoint).
- [ ] Cross-tenant and cross-project IDOR tests.
- [ ] Token expiry, refresh and logout tests.

### Exit Criteria

- [ ] An FDE of tenant A cannot see, list or infer any resource of tenant B, or of a tenant-A project they are not assigned to.
- [ ] Admin APIs reject FDE tokens, and FDE APIs reject Admin-only actions.
- [ ] Every login, role change and project assignment is recorded in the audit table.

## Design pages to read

- [TD §7. Database design](../design/07-database-design.md)
- [TD §8. API design](../design/08-api-design.md)
- [TD §9. Frontend design (Next.js + Refine)](../design/09-frontend-design.md)
- [TD §16. Security and multi-tenancy](../design/16-security-and-multi-tenancy.md)

## Issues in this phase

Each issue contains inputs/validation, outputs/checks, safety checks, edge cases and test cases.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#47](https://github.com/solutionsDigibull/BAM/issues/47) | `E2.1` | Keycloak realm, clients, roles, tenant mapper, realm export | A Platform | M | P0 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39), [#31](https://github.com/solutionsDigibull/BAM/issues/31) |
| [#48](https://github.com/solutionsDigibull/BAM/issues/48) | `E2.2` | FastAPI JWT validation + authorization chain | A Platform | M | P0 | M0 | [#47](https://github.com/solutionsDigibull/BAM/issues/47), [#36](https://github.com/solutionsDigibull/BAM/issues/36) |
| [#49](https://github.com/solutionsDigibull/BAM/issues/49) | `E2.3` | Tenants + users mirror + super-admin tenant APIs | A Platform | S | P0 | M0 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |
| [#50](https://github.com/solutionsDigibull/BAM/issues/50) | `E2.4` | RBAC APIs: invite users, roles, project assignment | A Platform | S | P0 | M0 | [#49](https://github.com/solutionsDigibull/BAM/issues/49) |
| [#51](https://github.com/solutionsDigibull/BAM/issues/51) | `E2.5` | Admin and FDE login entry points (Auth.js Keycloak) | D Frontend | S | P0 | M0 | [#44](https://github.com/solutionsDigibull/BAM/issues/44), [#47](https://github.com/solutionsDigibull/BAM/issues/47) |
| [#52](https://github.com/solutionsDigibull/BAM/issues/52) | `E2.6` | Users & Roles screen (Admin) | D Frontend | S | P0 | M0 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#50](https://github.com/solutionsDigibull/BAM/issues/50) |
| [#53](https://github.com/solutionsDigibull/BAM/issues/53) | `E2.7` | RBAC matrix + IDOR test suite | A Platform | S | P0 | M0 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |

---
[← Phase 1](phase-01-repository-foundation-and-containerized-dev-stack.md)   [Phases index](README.md)   [Phase 3 →](phase-03-admin-portal-projects-and-masters.md)
