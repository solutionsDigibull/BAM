# SBAM upgrade plan: current design, proposed change, why

Read this after `01-findings.md`. Each row says what exists today, what I propose, what it does, and why.
Sized S (< 1 day), M (1-3 days), L (> 3 days). Nothing here is started; this is for your verdict.
It leans on well-established OSS and platform features (Postgres RLS, Temporal patterns, pglast, gVisor/Firecracker,
DOMPurify) rather than copying a competitor. I did not clone competitor repositories; say so if you want that pass.

## Wave 0: before any staging/production deploy (blockers)

| # | Current | Proposed | What it does | Why | Size |
|---|---|---|---|---|---|
| U-01 | Certify/freeze/gate activities are stubs that always succeed, no env guard (F-01) | Stubs register only in dev/test; elsewhere they raise non-retryable `NOT_IMPLEMENTED`; test that prod worker has none | Makes "certified" mean something | Today a prod worker would freeze untested code | S |
| U-02 | Denylist pre-check; no AST/static gate; sandbox is a stub (F-02) | Allowlist AST gate, `pglast` SQL validation, runtime audit hook, sandbox as the only trust boundary (Firecracker/gVisor, no network, cgroups, seccomp) | Contains LLM-written code | A denylist loses to string-built `getattr`; PoC passes 9 payloads | L |
| U-03 | Approval signals Temporal, then commits (F-03); the Workflow-creation activity already re-checks the DB | Commit first, signal from an outbox relay | Removes the reverse race (legit approval failing as NOT_APPROVED) and the lock held across the Temporal call | `notification_outbox` pattern already in repo. Demoted to Wave 1 (P2) after the second pass | M |
| U-04 | `COGNEE_USER_SECRET` has a known default; `.env.example` placeholders accepted (F-09) | Required secrets with `:?`; lint rejects `change-me`/low entropy outside dev | Fail at boot instead of running with known keys | Predictable per-project credentials | S |
| U-05 | Dependency CVEs (F-10) | Bump pypdf, otel-api, monaco/dompurify, refine chain; add `pip-audit` + `pnpm audit --prod` to CI | Keeps known CVEs out | One critical + several high are runtime | S |
| U-18 | User-mirror refresh updates one row from every parallel request (N-01); 37% of requests 500 right after login in the repro | One atomic `UPDATE ... WHERE last_session_id IS DISTINCT FROM :sid`; audit LOGIN only when it changed a row | No spurious 500s after sign-in | Reproduced live; user-visible on every login | S |

## Wave 1: security hardening

| # | Current | Proposed | What it does | Why | Size |
|---|---|---|---|---|---|
| U-06 | Roles come from the token; mirror only blocks deactivated users (F-04) | Intersect token roles with DB `portal_roles`, 15-30 s cache; access token <= 5 min; Keycloak back-channel logout | Demotion works immediately | Docs promise "apply at once"; today it is token lifetime | M |
| U-07 | Tenant isolation by repository only (F-06) | Non-owner DB role + `FORCE ROW LEVEL SECURITY` + `SET LOCAL app.tenant_id` | DB refuses cross-tenant rows even if app code is wrong | One missed query is a cross-tenant leak; defense in depth is standard for multi-tenant SaaS | M-L |
| U-08 | Triggers exist for audit_log, SOW content, master/filetype versions, manifests, but the runtime role owns the tables and can disable them (F-07, live-confirmed) | Separate owner/migration role from a DML-only runtime role, `REVOKE UPDATE,DELETE` on `audit_log`, triggers on the remaining certified tables, hash chain | Tamper-evident audit and immutable certified versions even if the API process is compromised | Golden rule 'immutable (423)' should survive a bug or a rogue operator | M |
| U-09 | No rate limits except knowledge reads, in-process (F-08) | Caddy rate limit + per-tenant/user token bucket on LLM-triggering routes; aggregate rejection audit rows | Stops abuse, flooding and cost runaway | Unbounded audit growth and LLM spend are reachable by any FDE | M |
| U-10 | Invite 409 reveals emails across tenants (F-05) | Per-tenant uniqueness or neutral response; throttle | Removes enumeration oracle | Cross-tenant info leak by design | S |
| U-11 | CSP without `script-src`; blocklist paste cleaner (F-11) | Nonce CSP in `proxy.ts`; DOMPurify allowlist | Second layer against XSS | LLM/user content is rendered in rich editor and code viewer | M |
| U-12 | Upload token key derived from hook secret (F-14) | Independent secrets; hooks on private network with mTLS or request signing | Limits blast radius of a leaked secret | Key reuse across two trust roles | S |

## Wave 2: reliability and operability

| # | Current | Proposed | What it does | Why | Size |
|---|---|---|---|---|---|
| U-19 | clamd scan, Keycloak admin and JWKS calls block sync handlers; 45 parallel imports stall the API while `/health/live` stays green (N-02) | Move scans to a Temporal activity or a bounded executor with a deadline; readiness reflects saturation | One slow dependency cannot take the API down | Reproduced live | M |
| U-20 | Keycloak realm has no password policy, MFA or auth events (N-06) | Password policy, mandatory TOTP/WebAuthn for admin roles, event logging to the audit sink | Protects the accounts that approve code generation | TD §16 lists MFA as only optional | S |


| # | Current | Proposed | What it does | Why | Size |
|---|---|---|---|---|---|
| U-13 | Auth does JWKS + 2 DB sessions per request; default pool (F-12) | Single session per request, short auth-state cache, explicit pool sizing, load test with k6 | Lower latency and no pool starvation | 40 threads against a 15-connection pool | M |
| U-14 | Keycloak writes inside a DB transaction with a tenant lock (F-13) | Outbox/saga + reconciler | No lock held during HTTP; no drift | Partial failure leaves Keycloak and DB disagreeing | M |
| U-15 | Cursor secret random per process (F-21); in-process limiter | Required shared secret when >1 replica; shared limiter state (Postgres or edge) | Works horizontally | Breaks on restart / second replica | S |
| U-16 | Unpaginated admin lists; silent cap in `list()` (F-18, F-06) | One cursor contract everywhere; `list()` raises or paginates instead of truncating | Predictable API | Silent truncation hides rows | S |
| U-17 | Default branch `main` 459 commits behind `dev` (F-22) | Make `dev` the default or fast-forward `main` on a release cadence; run CI/scans on the default branch | Scanners, contributors and #357 see the real code | Process drift hides regressions | S |

## Design questions I would put to the team (not decided)

1. **Sync SQLAlchemy + threadpool vs async.** Sync is simpler and fine for a build-time tool. The cost is the pool
   math in F-12. Recommend: stay sync, fix pool and sessions; revisit async only if load tests demand it.
2. **Local-disk storage vs S3 by default.** Both exist behind `StorageBackend`. Recommend S3-compatible (MinIO) for
   any multi-replica deployment; local disk is single-node only.
3. **Keycloak as both IdP and tenant directory.** Organizations alias = tenant ID couples tenancy to Keycloak.
   Fine for now; the saga in U-14 contains the coupling.
4. **Segregation of duties on SOW approval.** The same FDE can edit and approve. If customers ask for four-eyes,
   add a second approver role. Product decision, not a bug.
5. **LLM prompt-injection model.** Documents feed SOW generation which feeds codegen. I have not tested it. Recommend
   a red-team pass with promptfoo (already in the repo) once U-02 lands.

## Suggested order

U-01, U-04, U-05 first (a day, no design risk). Then U-03, U-06, U-08. U-02 and U-07 are the large ones and need
design review; start them in parallel by different people. Wave 2 can follow in any order.
