# GitHub issue drafts (NOT filed)

Nothing here has been created on `solutionsDigibull/BAM`. Issues are visible to the whole team, so they are held
until you approve. Titles follow the repo convention (`[A-n] ...`); labels use the repo's scheme. Each body
should link `docs/audit/01-findings.md#f-xx`. Duplicates checked against open and closed issue titles
(#368 Authlib bump, #137 security suite, #53 RBAC/IDOR matrix are related but none covers these).

| Draft | Title | Labels | Priority | Size |
|---|---|---|---|---|
| A-01 | [A-01] Refuse stub activities (gates, test, certify, freeze) outside dev/test | `type:story` `area:platform` `priority:P0` | P0 | S |
| A-02 | [A-02] Codegen safety: allowlist AST gate, pglast SQL validation, sandbox as the boundary (supersedes #117/#119/#120 scope check) | `type:story` `area:build` `priority:P0` | P0 | L |
| A-03 | [A-03] SOW approve: signal after commit via outbox (reverse race + lock held across Temporal call) | `type:story` `area:sow` `priority:P2` | P2 | M |
| A-04 | [A-04] Enforce DB `portal_roles` in `current_user` (demotion lag up to 300 s) | `type:story` `area:identity` `priority:P2` | P2 | M |
| A-05 | [A-05] Invite endpoint leaks emails across tenants | `type:story` `area:identity` `priority:P2` | P2 | S |
| A-06 | [A-06] Postgres Row-Level Security for tenant isolation | `type:story` `area:platform` `priority:P1` | P1 | L |
| A-07 | [A-07] Split DB owner and runtime roles; REVOKE on `audit_log`; triggers on remaining certified tables | `type:story` `area:platform` `priority:P2` | P2 | M |
| A-08 | [A-08] Rate limiting and audit-flood protection | `type:story` `area:platform` `priority:P1` | P1 | M |
| A-09 | [A-09] Required secrets: remove `COGNEE_USER_SECRET` default, lint `change-me` values | `type:story` `area:platform` `priority:P1` | P1 | S |
| A-10 | [A-10] Dependency CVEs: pypdf, otel-api, dompurify/monaco, refine chain; add audits to CI | `type:chore` `priority:P1` | P1 | S |
| A-11 | [A-11] Nonce-based CSP and DOMPurify allowlist for pasted SOW HTML | `type:story` `area:client` `priority:P2` | P2 | M |
| A-12 | [A-12] Auth request path: one session, auth-state cache, explicit DB pool sizing, load test | `type:story` `area:platform` `priority:P2` | P2 | M |
| A-13 | [A-13] Keycloak writes outside DB transactions (saga + reconciler) | `type:story` `area:identity` `priority:P2` | P2 | M |
| A-14 | [A-14] Separate upload-token key from tusd hook secret | `type:story` `area:ingestion` `priority:P2` | P2 | S |
| A-16 | [A-16] Bug: parallel API calls right after login return 500 (`StaleDataError` in user-mirror refresh); 37% failure in repro | `type:story` `area:identity` `priority:P1` | P1 | S |
| A-17 | [A-17] Bulkhead blocking calls (clamd scan, Keycloak admin, JWKS): 45 parallel imports stall the whole API | `type:story` `area:platform` `priority:P2` | P2 | M |
| A-18 | [A-18] Keycloak realm: password policy, mandatory MFA for admins, auth events | `type:story` `area:identity` `priority:P2` | P2 | S |
| A-19 | [A-19] Masters import: stream/validate before materializing rows; 413 for chunked oversize bodies | `type:chore` `area:masters` `priority:P3` | P3 | S |
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

## Review comment, not an issue (post on the PR when `353-fde-citations` opens)

N-07: `stamp_fde` keeps an existing `(author, date, note)` stamp wherever it appears, so an FDE can copy a
colleague's stamp onto a new statement. Bind the stamp to the statement (path + text hash) or sign it with an HMAC.
Decide whether an FDE-authored citation should satisfy "every statement cites a source" at all.

Evidence for A-16, A-17, A-04, A-05, A-07 and the audit-flood part of A-08 is reproducible with the scripts in
`poc/live/` (see `04-second-pass.md`).
