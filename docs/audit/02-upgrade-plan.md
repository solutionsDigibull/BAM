# SBAM upgrade plan: current design, proposed change, why

Read after `01-findings.md`. Each row: what exists today, what I propose, what it does, why, size (S < 1 day,
M 1-3 days, L > 3 days) and the issue draft in `03-issue-drafts.md`. Nothing is started; this is for your verdict.
The proposals use established platform features (Postgres RLS and role separation, Temporal outbox patterns,
`pglast`, Firecracker/gVisor isolation, DOMPurify, rlimits) rather than copying a competitor product; I did not
clone competitor repositories (say so if you want that research pass).

## Wave 0: cheap, reachable today, or blocks any deploy

| # | Current | Proposed | What it does | Why | Size | Issue |
|---|---|---|---|---|---|---|
| U-01 | Gates, tests, certify and freeze are stubs that always succeed, no env guard (F-01) | Stubs register only in dev/test; elsewhere raise non-retryable `NOT_IMPLEMENTED`; test that a production worker serves none | Makes "certified" mean something | `compose.deploy.yaml` runs these workers; the LLM mock already has the same guard | S | A-01 |
| U-02 | `pypdf 6.20.1` parses uploaded PDFs in the shared ingest worker; a 14 MB PDF = 10 s, 523 MB (F-24) | Bump to `>=6.21.2` now | Crafted PDFs fail fast (`LimitReachedError`) | Reproduced; reachable by any FDE; one-line fix | S | A-21 step 1 |
| U-03 | User-mirror refresh races on the first calls after login: 37% of requests 500 in repro (N-01) | One atomic `UPDATE ... WHERE last_session_id IS DISTINCT FROM :sid`; audit LOGIN only when a row changed | No spurious 500s after sign-in | Reproduced; user-visible on every login | S | A-16 |
| U-04 | `COGNEE_USER_SECRET` has a known default; `.env.example` placeholders are accepted (F-09) | `:?required`; `compose_lint` rejects `change-me`/low-entropy secrets in deployed envs | Fails at boot instead of running with known keys | Predictable per-project Cognee credentials | S | A-09 |
| U-05 | Other dependency CVEs: dompurify (critical, client runtime), path-to-regexp, otel-api on the request path, others (F-10) | Bump; add `pip-audit` and `pnpm audit --prod` to CI with a reviewed allowlist; Renovate/Dependabot | Keeps known CVEs out | 1 critical and several high are runtime | S | A-10 |

## Wave 1: before the sandbox ships and before go-live

| # | Current | Proposed | What it does | Why | Size | Issue |
|---|---|---|---|---|---|---|
| U-06 | Denylist pre-check; AST gate, static gate, runtime hook and sandbox are stubs (F-02) | Allowlist AST gate, `pglast` SQL validation, runtime audit hook, sandbox as the only trust boundary (Firecracker/CubeSandbox or gVisor, no network, cgroups, seccomp) | Contains LLM-written code | 6 Python escapes pass the pre-check (PoC); a denylist loses to string-built `getattr` | L | A-02 |
| U-07 | Corrector LLM can patch the FDE-reviewed ISATVON, then the build certifies (F-23) | Classify patches; behaviour-changing ones go to `NEEDS_FDE_REVIEW`; certificate records approved and final ISATVON hashes | The certified skill is the one the FDE approved | Otherwise a model can loosen the spec to pass its own tests | M | A-20 |
| U-08 | Roles come from the token; mirror only blocks deactivated users (F-04) | Intersect token roles with DB `portal_roles` (15-30 s cache); honor `not-before`/back-channel logout | Demotion works on the next request | Live: demoted admin still got 200; docs promise "apply at once" | M | A-04 |
| U-09 | Triggers exist, but the runtime role owns the tables and can disable them (F-07) | Split owner (migrations) and DML-only runtime role; `REVOKE UPDATE,DELETE` on `audit_log`; triggers on remaining certified tables; optional hash chain | Audit and immutability survive a compromised API process | Live: `DISABLE TRIGGER` then `UPDATE audit_log` succeeded | M | A-07 |
| U-10 | Rate limits only on knowledge reads, in-process; every rejection writes an audit row (F-08, N-05) | Caddy rate limit; per-user/tenant token bucket on LLM routes (ties to #421); one audit row per request; throttle denial rows | Stops abuse, audit flooding and cost runaway | 125 requests wrote 725 audit rows | M | A-08 |
| U-11 | Invite 409 reveals emails across tenants (F-05) | Per-tenant uniqueness or neutral response; throttle | Removes the enumeration oracle | Live: 409 for a foreign-tenant email | S | A-05 |
| U-12 | Keycloak realm: no password policy, MFA or auth events (N-06) | Password policy; mandatory TOTP/WebAuthn for admin roles; login and admin events to the audit sink | Protects the accounts that approve code generation | TD §16 lists MFA as only optional | S | A-18 |
| U-13 | clamd scan, Keycloak admin and JWKS calls block sync handlers (N-02) | Scan as a Temporal activity or a bounded executor with a deadline; readiness reflects saturation | One slow dependency cannot take the API down | 45 parallel imports stalled the API while health stayed green | M | A-17 |
| U-14 | Approval signals Temporal, then commits, lock held across the call (F-03) | Commit first, signal from an outbox relay | Removes the reverse race and lock hold time | `notification_outbox` already exists; no phantom Workflow exists today because the activity re-checks | M | A-03 |

## Wave 2: hardening and hygiene

| # | Current | Proposed | What it does | Why | Size | Issue |
|---|---|---|---|---|---|---|
| U-15 | Tenant isolation by repository only; no RLS (F-06) | Non-owner role (needs U-09) + `FORCE ROW LEVEL SECURITY` + `SET LOCAL app.tenant_id`; fix `list()` truncation and project check in `update()` | DB refuses foreign rows even if app code is wrong | One missed query would leak; no leak found today | L | A-06 |
| U-16 | Parsers run in-process in the shared ingest worker (F-24 step 2) | Per-document subprocess with rlimits and a deadline; per-tenant ingest concurrency | A poison file cannot starve or kill the worker | 6 of 7 pypdf advisories were this class | M | A-21 step 2 |
| U-17 | CSP without `script-src`; blocklist paste cleaner (F-11) | Nonce CSP in `proxy.ts`; DOMPurify allowlist | Second layer against XSS | LLM and user content render in a rich editor and code viewer | M | A-11 |
| U-18 | Upload token key derived from hook secret (F-14) | Independent secrets; hooks on a private network, optionally signed | Limits blast radius of a leaked secret | Key reuse across two trust roles | S | A-14 |
| U-19 | Auth path opens 2 sessions per request (F-12) | One request-scoped session; short auth-state cache; explicit pool size; k6 load test in CI | Less work per request | Steady state did not fail, so low priority | M | A-12 |
| U-20 | Keycloak writes inside a DB transaction holding the tenant lock (F-13) | Outbox/saga plus reconciler | No HTTP under lock; no drift | Partial failure leaves Keycloak and DB disagreeing | M | A-13 |
| U-21 | Workbook import reads all rows first; chunked oversize gives 400 (N-03, N-04) | Validate from the stream, cap rows; translate the limit exception to 413 | Cheap rejection of bad files; contract-correct errors | 14 MB workbook = 18.5 s before rejection | S | A-19 |
| U-22 | Cursor secret random per process; unpaginated lists; `ENABLE_PING` default; `/openapi.json` open; `main` behind `dev` (F-15, F-17..F-22, N-08) | Hygiene batch | Predictable, safer defaults | Small individually | S | A-15 |

## Design questions for the team (not decided)

1. **Sync SQLAlchemy and sync handlers vs async.** Sync is simpler and fits a build-time tool. The cost is the
   40-thread pool (N-02). Recommend: stay sync, add bulkheads and one session per request; go async only where
   profiling demands.
2. **Local-disk storage vs S3 by default.** Both exist behind `StorageBackend`. Recommend S3-compatible (MinIO) for
   any multi-replica deployment; local disk is single-node only.
3. **Keycloak as IdP and tenant directory** (Organizations alias = tenant UUID). Fine; contain the two-system writes
   (U-20).
4. **Segregation of duties on SOW approval.** The same FDE can edit and approve. Ask whether customers require a
   second approver (product decision, not a bug).
5. **May one person belong to two tenants?** Decides U-11.
6. **Where do `state_queries` run at runtime, with which role?** Decides how serious the SQL half of F-02 is.
7. **LLM prompt injection.** Documents feed SOW generation, which feeds codegen. Untested. Run a promptfoo red-team
   pass (the tool is already in the repo) once U-06 lands.

## Suggested order

Wave 0 first (about two days in total, low design risk; U-02 first because it is reachable today). Then U-08, U-09,
U-10, U-11, U-12, U-13 in any order. U-06, U-07 and U-15 need design review and should start in parallel with
different owners. Wave 2 follows as capacity allows.
