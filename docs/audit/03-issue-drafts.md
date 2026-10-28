# GitHub issue drafts (NOT filed)

Nothing here has been created on `solutionsDigibull/BAM`. Issues are visible to the whole team, so they are held
until you approve. Titles follow the repo convention (`[A-n] ...`); labels use the repo's scheme. Each body
should link `docs/audit/01-findings.md#f-xx`. Duplicates checked against open and closed issue titles
(#368 Authlib bump, #137 security suite, #53 RBAC/IDOR matrix are related but none covers these).

| Draft | Title | Labels | Priority | Size |
|---|---|---|---|---|
| A-01 | [A-01] Refuse stub activities (gates, test, certify, freeze) outside dev/test | `type:story` `area:platform` `priority:P0` | P0 | S |
| A-02 | [A-02] Codegen safety: allowlist AST gate, pglast SQL validation, sandbox as the boundary (supersedes #117/#119/#120 scope check) | `type:story` `area:build` `priority:P0` | P0 | L |
| A-03 | [A-03] SOW approve: outbox instead of signal-before-commit | `type:story` `area:sow` `priority:P1` | P1 | M |
| A-04 | [A-04] Enforce DB `portal_roles` in `current_user`; shorten token lifetime | `type:story` `area:identity` `priority:P1` | P1 | M |
| A-05 | [A-05] Invite endpoint leaks emails across tenants | `type:story` `area:identity` `priority:P1` | P1 | S |
| A-06 | [A-06] Postgres Row-Level Security for tenant isolation | `type:story` `area:platform` `priority:P1` | P1 | L |
| A-07 | [A-07] DB-enforced immutability: frozen triggers, append-only `audit_log`, `sow_versions.is_locked` with backfill | `type:story` `area:platform` `priority:P1` | P1 | M |
| A-08 | [A-08] Rate limiting and audit-flood protection | `type:story` `area:platform` `priority:P1` | P1 | M |
| A-09 | [A-09] Required secrets: remove `COGNEE_USER_SECRET` default, lint `change-me` values | `type:story` `area:platform` `priority:P1` | P1 | S |
| A-10 | [A-10] Dependency CVEs: pypdf, otel-api, dompurify/monaco, refine chain; add audits to CI | `type:chore` `priority:P1` | P1 | S |
| A-11 | [A-11] Nonce-based CSP and DOMPurify allowlist for pasted SOW HTML | `type:story` `area:client` `priority:P2` | P2 | M |
| A-12 | [A-12] Auth request path: one session, auth-state cache, explicit DB pool sizing, load test | `type:story` `area:platform` `priority:P2` | P2 | M |
| A-13 | [A-13] Keycloak writes outside DB transactions (saga + reconciler) | `type:story` `area:identity` `priority:P2` | P2 | M |
| A-14 | [A-14] Separate upload-token key from tusd hook secret | `type:story` `area:ingestion` `priority:P2` | P2 | S |
| A-15 | [A-15] Low-priority batch: `/admin/filetypes` policy, unpaginated lists, `ENABLE_PING` lint, cursor secret requirement, upload-limit race | `type:chore` `priority:P2` | P2 | S |

## Body template (each issue)

```
**Finding:** F-xx in docs/audit/01-findings.md (confidence: PoC | Code | Live?)
**Where:** file:line
**Current behavior / evidence:** ...
**Why it matters:** impact on a real deployment
**Proposed change and comparison:** current approach vs proposed, with the trade-off
**Acceptance criteria:** testable list (with test IDs per AGENTS.md)
**Blocked by:** ...
```

A-02 touches D3 hot files (`server/workers/.../pipeline/**` is not touched, but `build/` and contracts may be);
A-06/A-07 add Alembic migrations (one head rule applies); A-08 touches `core/**` and `compose/proxy` (D1/D2 owners).
Per AGENTS.md those owners should review.
