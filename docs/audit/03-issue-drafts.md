# GitHub issue drafts (NOT filed)

Nothing here has been created on `solutionsDigibull/BAM`. Issues are visible to the whole team, so they are held
until approved. Titles use the repo convention (`[A-nn] ...`, the `A-` prefix is for this audit; rename to an epic
number when filing). Labels follow the repo scheme. Findings reference `01-findings.md`. Related existing issues
(none duplicate these): #368 (Authlib bump), #137 (E15.3 security suite), #53 (RBAC/IDOR matrix), #117/#119/#120
(AST/static gates, import resolution), #421 (LLM cost control), #357 (CI on `main`).

Per AGENTS.md, each issue is one PR in one lane. Hot-file owners who must review are listed per issue. `Blocked by`
lists only real ordering constraints.

| Draft | Title | Priority | Size | Lane / hot files touched |
|---|---|---|---|---|
| A-01 | Refuse stub activities outside dev/test | P0 | S | D3 (`workers/.../bootstrap.py`, `registry.py` are D3-owned) |
| A-21 | Crafted PDF exhausts the ingest worker: bump pypdf now, isolate parsers | P1 | S then M | D2 (ingestion), D1 (lockfile) |
| A-02 | Codegen safety: allowlist AST gate, SQL validation, sandbox boundary | P1 | L | D3, D2 (compose sandbox) |
| A-16 | Bug: parallel calls after login return 500 | P1 | S | D1 (`identity/service.py`, `core/auth`) |
| A-17 | Bulkhead blocking external calls (clamd, Keycloak, JWKS) | P2 | M | D1 / D2 |
| A-04 | Enforce DB `portal_roles` in `current_user` | P2 | M | D1 (`core/**`) |
| A-05 | Invite endpoint is a cross-tenant email oracle | P2 | S | D1 |
| A-07 | Split DB owner/runtime roles; protect `audit_log`; remaining triggers | P2 | M | D1 + D3 (audit, state tables), D2 (`compose/postgres`) |
| A-08 | Rate limiting and audit-flood protection | P2 | M | D1 (`core/**`), D2 (`compose/proxy`) |
| A-09 | Required secrets; lint `change-me` values | P2 | S | D2 (`compose*.yaml`, `.env.example`) |
| A-10 | Dependency CVEs and CI audits | P2 | S | D1 (`pyproject.toml`, lockfiles, `.github/**`) |
| A-18 | Keycloak realm hardening | P2 | S | D1 / D2 (`compose/keycloak`) |
| A-03 | SOW approve: signal after commit via outbox | P2 | M | D3 (`pipeline/**`), D2 (SOW) |
| A-06 | Postgres row-level security | P2 | L | D1 (`db/**`, migration base) |
| A-11 | Nonce CSP and DOMPurify allowlist | P3 | M | D1 (`proxy.ts`, `next.config.ts`) |
| A-12 | One session per request; auth-state cache | P3 | M | D1 |
| A-13 | Keycloak writes outside DB transactions | P3 | M | D1 |
| A-14 | Separate upload-token key from the hook secret | P3 | S | D2 (ingestion) |
| A-19 | Masters import: validate before materialising; 413 for chunked | P3 | S | D2 (masters) |
| A-20 | Corrector may change the approved ISATVON and certify without re-review | P2 | M | D3 (`build/codegen/correction.py`, pipeline) |
| A-15 | Hygiene batch (F-15, F-17..F-22, N-08) | P3 | S | mixed |

Order: A-21 (the version bump), A-01, A-16, A-09, A-10 first (small, low design risk). Then A-04, A-05, A-08, A-17, A-18. A-02, A-06, A-07, A-20
need design review and should start in parallel with different owners.

---

## A-01 [A-01] Refuse stub activities (gates, test, certify, freeze) outside dev/test
**Labels:** `type:story` `area:platform` `priority:P0` `size:S` · **Finding:** F-01 (Code)
**Where:** `server/workers/sbam_workers/build/stubs.py:55-80`, `test/stubs.py`, `certify/stubs.py`, `registry.py`,
`bootstrap.py`.
**Problem:** `ast_gate`, `static_gate`, `generate_tests`, `build_promptfoo_suite`, `run_sandbox_tests`,
`certify_sub_skill`, `certify_macros`, `validate_workflow`, `certify_workflow`, `freeze_and_version` always
succeed; `freeze_and_version` returns `1.0.0`. `compose.deploy.yaml` runs `worker-build/test/certify`. A deployed
worker would certify and freeze code that was never gated or tested.
**Current vs proposed:** today the LLM mock is refused outside dev (`Settings._refuse_mock_provider_outside_dev`);
stubs have no equivalent. Proposed: the same fail-closed rule for stubs. Alternative considered, deleting stubs:
rejected, the pipeline skeleton and replay tests depend on them.
**Acceptance:**
- With `SBAM_ENV` in {staging, production, prod}, a stub activity raises non-retryable `ApplicationError(
  "NOT_IMPLEMENTED")` (or the worker refuses to start when it would serve one).
- In development/test behaviour is unchanged; replay tests pass.
- Test `test_a01_t01_prod_worker_serves_no_stub` enumerates registered activities per queue and fails if any is a
  stub in a production env. Test per stub family for the NOT_IMPLEMENTED path.
**Safety checks:** no new env var needed (reuse `SBAM_ENV`); the guard fails closed when `SBAM_ENV` is unset in a
deployed compose file (add to `compose_lint.py`).

## A-02 [A-02] Codegen safety: allowlist AST gate, SQL validation, sandbox as the only boundary
**Labels:** `type:story` `area:build` `priority:P1` `size:L` · **Finding:** F-02 (PoC)
**Blocked by:** coordinate with #117, #119, #120, #123 (this should be folded into them, not duplicated; check scope
before filing).
**Evidence:** `docs/audit/poc/poc_ast_bypass.py` runs the real `precheck()`: 6 Python escapes and 3 `state_queries`
return no findings (string-built `getattr`, `open()`, class walk without `__subclasses__`, `globals()`, unbounded
loop, memory bomb; `SELECT ... FROM sbam.users`, `pg_read_file`, `pg_sleep`).
**Current vs proposed:** denylist by name (`FORBIDDEN_NAMES`, `FORBIDDEN_ATTRS`) and a regex for `state_queries`
(`startswith("SELECT")`, one `%s`, no `;`). Denylists lose to indirection. Proposed:
1. The pre-check stays a fast fail but is allowlist-based (permitted node types, builtins and attribute names).
2. `state_queries` parsed with `pglast`, restricted to SELECT of the handoff relation, no functions outside an
   allowlist, executed under a read-only role scoped to that schema with `statement_timeout`.
3. The sandbox is the boundary: Firecracker/CubeSandbox (certifying) or gVisor, no network except the declared mock
   API, cgroup CPU/memory/pids limits, read-only root, seccomp, wall-clock kill.
4. Runtime audit hook (#120) for imports and file/network access.
Comparison: running LLM code in a restricted-`exec` Python sandbox is known to be bypassable; process isolation with
a kernel boundary is the accepted approach.
**Acceptance:** each payload in the PoC is rejected by the pre-check with a stable finding code (tests named
`test_a02_t01..t09`); a body that evades the static layer is still contained by the sandbox (escape tests: read
`/etc/passwd`, open a socket, fork-bomb, allocate 10 GB, `sleep` past the deadline); the `local-docker` mode never
certifies (golden rule). Document the threat model in an ADR.

## A-16 [A-16] Bug: parallel API calls right after login return 500 (user-mirror refresh race)
**Labels:** `type:story` `area:identity` `priority:P1` `size:S` · **Finding:** N-01 (PoC)
**Where:** `server/api/sbam_api/identity/service.py` `SqlUserDirectory._refresh` (~L421), called from
`core/auth.py:current_user` on every request.
**Problem:** a new `sid` counts as a new login; every parallel request updates the same `users` row with an
optimistic `row_version` check; all but one raise `StaleDataError` (unhandled, becomes 500). Repro
`poc/live/bugA.py`: 45 of 120 requests failed when 8 calls followed each of 15 logins.
**Proposed:** a single atomic `UPDATE users SET last_session_id=:sid, last_login_at=now() WHERE id=:id AND
last_session_id IS DISTINCT FROM :sid`; emit the LOGIN audit event only when `rowcount == 1`. Same treatment for the
other mirrored fields (email, display name): `UPDATE ... WHERE <col> IS DISTINCT FROM :v`. Comparison: retrying on
`StaleDataError` also works but still burns a transaction per request and can double-audit.
**Acceptance:** `test_a16_t01_parallel_first_requests_all_succeed` (16 threads, fresh `sid`, expect all 200, exactly
one LOGIN audit row); `test_a16_t02_second_login_audited_once`; no change to deactivated/suspended behaviour.

## A-17 [A-17] Bulkhead blocking external calls (clamd scan, Keycloak admin, JWKS)
**Labels:** `type:story` `area:platform` `priority:P2` `size:M` · **Finding:** N-02 (PoC)
**Problem:** sync handlers call clamd (up to 120 s with retries), Keycloak admin (inside a DB transaction) and the
JWKS endpoint (3 s). 45 parallel imports against a hung clamd made `GET /me` by another user time out while
`/health/live` stayed 200 (`poc/live/thread_exhaust.py`).
**Proposed:** (a) move the masters/filetype scan and parse into a Temporal activity returning 202 with a run, as the
golden rules already require for long work; or (b) at minimum a bounded executor per dependency with a semaphore,
a total deadline below 10 s for request-path calls, and 503 `SCAN_UNAVAILABLE` when saturated; (c) `/health/ready`
reports thread-pool saturation and dependency breakers so the orchestrator can act.
**Acceptance:** with clamd hung, other endpoints stay under 200 ms p95 while imports queue or fail fast; test with
the hung-listener fixture from `poc/live/hung_clamd.py`.
**Note:** touches `masters/` (D2) and `core/` (D1); coordinate.

## A-04 [A-04] Enforce DB `portal_roles` in `current_user`
**Labels:** `type:story` `area:identity` `priority:P2` `size:M` · **Finding:** F-04 (PoC)
**Problem:** `admin()`/`fde()` trust JWT roles; the mirror only blocks `DEACTIVATED`. Live, a DB-demoted admin
still got 200 on `/admin/projects` (up to 300 s, the realm's `accessTokenLifespan`).
**Proposed:** compute effective roles as token roles intersected with `users.portal_roles` (cache 15-30 s keyed by
`(sub, sid)`, invalidated on role change); keep 5-minute tokens; honor Keycloak `not-before`/back-channel logout.
Comparison: token introspection on every request is exact but adds a Keycloak round trip per request; the DB
intersection reuses the lookup the mirror already does.
**Acceptance:** `test_a04_t01_demoted_admin_gets_403_next_request`; promoted users are not granted a role the token
lacks; super-admin unaffected; docstring "apply at once" becomes true.

## A-05 [A-05] Invite endpoint is a cross-tenant email oracle
**Labels:** `type:story` `area:identity` `priority:P2` `size:S` · **Finding:** F-05 (PoC)
**Problem:** `identity/service.py:199` `email_taken()` is global; 409 `USER_EXISTS` reveals that an address exists in
any tenant. **Decision needed:** may one person belong to two tenants?
**Proposed:** if yes, make uniqueness `(tenant_id, lower(email))` and answer 409 only for the caller's tenant; if no,
return the same 202/201 body for taken and free addresses and notify out of band. Rate-limit invites per admin.
**Acceptance:** identical status and body for foreign-tenant and unknown addresses (`test_a05_t01`); unique
constraint matches the decision; migration reversible (single Alembic head).

## A-07 [A-07] Split DB owner and runtime roles; protect `audit_log`; triggers on remaining certified tables
**Labels:** `type:story` `area:platform` `priority:P2` `size:M` · **Finding:** F-07 (PoC)
**Problem:** the runtime role `sbam` owns the database and tables, so `ALTER TABLE ... DISABLE TRIGGER` followed by
`UPDATE audit_log` succeeds (`poc/live` transcript in `04-second-pass.md`). Triggers exist on `audit_log`,
`sow_versions` (content), master/filetype versions and `sub_skill_manifests`; none on `workflows`, `code_attempts`,
`artifacts` (has `is_frozen`), macro/sub-skill rows.
**Proposed:** `sbam_owner` (migrations, DDL) and `sbam_app` (DML only, no `TRIGGER`, no DDL); `REVOKE UPDATE, DELETE`
on `audit_log` from `sbam_app`; frozen triggers on every table with `is_frozen`; optional hash chain over audit rows.
Comparison: triggers alone defend against bugs, not against a compromised process; role separation defends both.
The `sow_versions.is_locked` column from TD §7.5 is unnecessary (content trigger + `state` already cover it); update
the doc instead of adding the column.
**Acceptance:** as `sbam_app`, `UPDATE`/`DELETE` on `audit_log` and `DISABLE TRIGGER` fail; migrations run as
`sbam_owner`; compose, CI and docs updated; one reversible migration.
**Hot files:** `compose/postgres/init.sh` (D2), audit module (D3), `migrations/env.py` (D1).

## A-08 [A-08] Rate limiting and audit-flood protection
**Labels:** `type:story` `area:platform` `priority:P2` `size:M` · **Finding:** F-08 / N-05 (PoC)
**Problem:** only knowledge graph reads are limited, in-process. 125 requests produced 725 audit rows; LLM-triggering
routes (`sow/regenerate`, `pipeline/start`, isatvon regenerate) have only idempotency keys.
**Proposed:** (1) Caddy `rate_limit` per IP on the portal host; (2) per-user/per-tenant token bucket for
LLM-triggering and write routes, tied to #421 budgets; (3) one audit row per `POST /uploads` request (list of
rejected files in `after`); (4) throttle `access_denied` audit writes per (actor, minute). Comparison: in-process
limiters do not hold across replicas; a proxy limit plus a Postgres-backed bucket for per-tenant limits avoids new
infrastructure (Redis is not an SBAM dependency).
**Acceptance:** `test_a08_t01` 25 reject-requests add <= 25 audit rows; 429 with `Retry-After` beyond the budget;
limits configurable by env with safe defaults.

## A-09 [A-09] Required secrets; lint `change-me` values
**Labels:** `type:story` `area:platform` `priority:P2` `size:S` · **Finding:** F-09 (Code)
**Problem:** `compose.yaml:404` defaults `COGNEE_USER_SECRET` to a known 38-character string that passes the
>= 32 check; `.env.example` ships `change-me-*` for every secret.
**Proposed:** `${COGNEE_USER_SECRET:?required}`; `compose_lint.py` fails any deployed env whose secrets contain
`change-me` or fall below an entropy/length floor; settings validators reject the same in non-dev `SBAM_ENV`.
**Acceptance:** `make compose-lint` fails on a `change-me` secret in staging/production; dev flow unchanged.

## A-21 [A-21] Crafted PDF exhausts the shared ingest worker: bump pypdf now, then isolate parsers
**Labels:** `type:story` `area:ingestion` `priority:P1` `size:S` (bump) + `size:M` (isolation) · **Finding:** F-24 (PoC)
**Where:** `server/workers/sbam_workers/ingest/loaders/pdf.py`, `server/workers/pyproject.toml` (`pypdf>=6`).
**Evidence:** `poc/pdf/mk.py` + `run.py`: 14 MB PDF with a 7M-element array: 10.3 s, 523 MB on pypdf 6.20.1;
`LimitReachedError` at once on 6.21.2. Worker `mem_limit: 1g`, queue shared across tenants.
**Step 1 (S):** require `pypdf>=6.21.2`, regenerate `uv.lock`, add a test that the two PoC files fail fast
(`test_a21_t01_array_bomb_rejected`, `t02_content_stream_bomb_rejected`) mapped to `ParseError` CORRUPT/LIMIT.
**Step 2 (M):** run each parse in a child process with `RLIMIT_AS`/`RLIMIT_CPU` and a wall-clock kill, returning the
result over a pipe; per-tenant concurrency limit on the ingest queue. Comparison: in-process parsing with
`try/except` cannot stop memory growth or a C-library crash; a subprocess turns both into a recorded `PARSE_FAILED`.
**Acceptance:** a 15 MB hostile PDF cannot raise worker RSS above the cap or delay other tenants' documents by more
than the configured deadline.

## A-10 [A-10] Dependency CVEs and CI audits
**Labels:** `type:chore` `priority:P2` `size:S` · **Finding:** F-10 (scanner)
**Do:** bump `pypdf` (>= 6.21.2, also covered by A-21 step 1), `opentelemetry-api` (>= 1.45.1), `litellm`, `filelock`, `setuptools`, `black`;
update `monaco-editor`/`dompurify`, the `@refinedev/antd` chain (`path-to-regexp`), `qs`; track `diskcache` (no fix)
and keep it unreachable. Add `pip-audit` and `pnpm audit --prod` to CI with a reviewed allowlist, and Renovate or
Dependabot. Relates to #368.
**Acceptance:** CI fails on a new high/critical runtime advisory; lockfiles regenerated per the lockfile rule.
**Hot files:** `pyproject.toml`, lockfiles, `.github/**` (D1).

## A-18 [A-18] Keycloak realm: password policy, MFA for admins, authentication events
**Labels:** `type:story` `area:identity` `priority:P2` `size:S` · **Finding:** N-06 (Code)
**Proposed:** realm `passwordPolicy` `length(12) and notRecentlyUsed and passwordBlacklist`; required TOTP or
WebAuthn for `sbam-admin` and `super-admin` (policy flag for FDEs); `eventsEnabled` and `adminEventsEnabled` with
a listener that forwards LOGIN_ERROR and role changes to the audit sink; update TD §16 (currently "MFA optional").
**Acceptance:** seed users still work in dev (flag off); realm export test asserts the settings in deployed envs.

## A-03 [A-03] SOW approve: signal after commit via outbox
**Labels:** `type:story` `area:sow` `priority:P2` `size:M` · **Finding:** F-03 (Code)
**Problem:** `sow/approve.py:253-260` signals Temporal, then commits, holding the `sows` row lock across the call.
`create_workflow` already rejects unapproved SOWs, so there is no phantom Workflow; the risk is a legitimate
approval failing as `NOT_APPROVED` when the activity reads before the commit, and lock hold time.
**Proposed:** commit approval plus an outbox row, then relay the signal with idempotent retry (`expect_stage` check
stays). Alternative: keep the order but make `create_workflow` retry `NOT_APPROVED` for a short window; rejected as
it hides the race and weakens the hash check.
**Acceptance:** `test_a03_t01_activity_never_sees_unapproved` (replay test with delayed commit); lock released
before the signal; Temporal-down still returns 503 and leaves the version unapproved or marked pending-signal.
**Hot files:** `pipeline/**` is D3-owned.

## A-06 [A-06] Postgres row-level security for tenant isolation
**Labels:** `type:story` `area:platform` `priority:P2` `size:L` · **Finding:** F-06 (hardening)
**Depends on:** A-07 (needs a non-owner runtime role).
**Problem:** no RLS (0 policies, 0 of 36 tables). One missed query is a cross-tenant leak. No leak was found; this is
defence in depth.
**Proposed:** `ENABLE`+`FORCE ROW LEVEL SECURITY` on every table with `tenant_id`; policy
`tenant_id = current_setting('app.tenant_id')::uuid`; set with `SET LOCAL` at transaction start from the verified
context; a super-admin bypass path that is explicit and audited. Also fix `TenantScopedRepository.list()` truncation
and make `update()` check the project. Comparison: app-layer scoping alone depends on every author; RLS adds a
second, independent layer at modest cost.
**Acceptance:** with the app-level filter deliberately removed in a test, queries return no foreign rows; workers and
migrations still run (they set the tenant per activity); tests for the RLS-off paths.

## A-11 [A-11] Nonce-based CSP and DOMPurify allowlist for pasted SOW HTML
**Labels:** `type:story` `area:client` `priority:P3` `size:M` · **Finding:** F-11 (Code)
**Proposed:** per-request nonce set in `proxy.ts`, `script-src 'self' 'nonce-...' 'strict-dynamic'`, `default-src
'self'`; replace `cleanPastedHtml` (blocklist) with DOMPurify and an explicit tag/attribute allowlist.
**Acceptance:** Playwright run with CSP enforcing shows no violations on the main flows; paste tests with event
handler attributes and `javascript:` links are stripped.

## A-12 [A-12] One session per request; short auth-state cache
**Labels:** `type:story` `area:platform` `priority:P3` `size:M` · **Finding:** F-12 (Code)
**Proposed:** a single request-scoped session shared by membership lookup, mirror sync and the handler; cache
`(sub, sid)` auth state for a few seconds; explicit `pool_size`/`max_overflow` and a load test (k6) as a CI job.
**Acceptance:** DB sessions per request drop from 3 to 1 on the auth path; no regression in the A-04 behaviour.

## A-13 [A-13] Keycloak writes outside DB transactions
**Labels:** `type:story` `area:identity` `priority:P3` `size:M` · **Finding:** F-13 (Code)
**Proposed:** DB is the source of truth; record the intended change and an outbox row, commit, then converge
Keycloak with retries and a reconciler that fixes drift. Avoid holding `lock_tenant` during HTTP.
**Acceptance:** a failing `logout` no longer leaves Keycloak and the DB disagreeing; tenant lock hold time bounded.

## A-14 [A-14] Separate upload-token key from the tusd hook secret
**Labels:** `type:story` `area:ingestion` `priority:P3` `size:S` · **Finding:** F-14 (Code)
**Proposed:** two independent secrets (`UPLOAD_TOKEN_KEY`, `TUSD_HOOK_SECRET`); hooks only on the private network,
optionally request-signed. **Acceptance:** rotating one does not invalidate the other; old tokens expire within TTL.

## A-19 [A-19] Masters import: validate before materialising rows; 413 for chunked oversize bodies
**Labels:** `type:chore` `area:masters` `priority:P3` `size:S` · **Findings:** N-03, N-04 (PoC)
**Proposed:** read sheet names and header rows from the stream first, cap rows per sheet to the template's size,
then stream rows; let `BodyLimitMiddleware` answer 413 for chunked bodies (translate its exception before FastAPI's
body-parse handler).
**Acceptance:** the 14 MB / 400k-row workbook is rejected in under 1 s; chunked 12 MB returns 413
`PAYLOAD_TOO_LARGE`; existing import tests pass.

## A-20 [A-20] Corrector may change the approved ISATVON and certify without FDE re-review
**Labels:** `type:story` `area:build` `priority:P2` `size:M` · **Finding:** F-23 (Code)
**Where:** `server/workers/sbam_workers/build/codegen/correction.py` (`apply_isatvon_patch`, `PATCH_ROOT`),
`pipeline/sub_skill_build.py` (re-manifest after correction, then `certify_sub_skill`).
**Problem:** the LLM corrector can patch `/isatvon_definition` to make failing tests pass; the loop continues to
certification with no human step. Tests were generated from the original definition, so loosening the definition
is a way to pass. **Unverified:** whether certification, the SRIM handoff or the UI flags a skill whose ISATVON
differs from what the FDE approved.
**Proposed:** classify patch ops (code-neutral vs behaviour-changing, by path allowlist); behaviour-changing patches
move the Sub-Skill to `NEEDS_FDE_REVIEW` (existing state) with the diff; certification stores
`approved_isatvon_sha256` and `final_isatvon_sha256`; the certificate shows both. Comparison: forbidding ISATVON
patches entirely is simpler but loses useful fixes (missing libraries are corrected this way today); classification
keeps them and adds a human gate only where the meaning changes.
**Acceptance:** `test_a20_t01_behaviour_patch_requires_review`, `t02_neutral_patch_continues`,
`t03_certificate_records_both_hashes`; replay tests pass (use `workflow.patched`).
**Hot files:** `pipeline/**` is D3-owned.

## A-15 [A-15] Hygiene batch
**Labels:** `type:chore` `priority:P3` `size:S` · **Findings:** F-15, F-17, F-18, F-19, F-20, F-21, F-22, N-08
`/admin/filetypes` -> `admin` or `fde` policy; one pagination contract; `ENABLE_PING` in `compose_lint`; confirm the
proxy does not log signed URLs; require `CURSOR_SECRET` when more than one replica; disable `/openapi.json` and
`/docs` when `SBAM_ENV` is production and add `Cache-Control: no-store` to authenticated JSON; lock the upload-slot
check with a per-project advisory lock; align `main` with `dev` (or make `dev` the default).

---

## Review comment, not an issue (post when `353-fde-citations` opens a PR)
N-07: `stamp_fde` keeps an existing `(author, date, note)` stamp wherever it appears, so an FDE can copy a
colleague's stamp onto a new statement. Bind the stamp to the statement (path + text hash) or sign it with an HMAC.
Decide whether an FDE-authored citation should satisfy "every statement cites a source".

## Reproducing evidence
`poc/live/` contains the scripts (README in `poc/README.md`). Issues A-16, A-17, A-04, A-05, A-07, A-08 and A-19 each
cite the script and the measured number.
