# SBAM security and design audit: consolidated findings (final)

Target: `origin/dev` at `d8b0dce` (2026-10-28). This file is the single source of truth. It merges the first pass,
the second pass (`04-second-pass.md` keeps the history of what was corrected and why) and the live pentest.

**Evidence tags.** `PoC` = reproduced by a script in `poc/` against a running API. `Code` = confirmed by reading
the cited code, not run. `Unverified` = plausible, needs a live stack or more information.
**Priorities.** P0 = blocks any staging/production deploy. P1 = fix this milestone. P2 = fix before go-live.
P3 = hygiene.

## 1. Scope and coverage (read this before trusting the absence of a finding)

| Area | Depth |
|---|---|
| Auth chain, identity, projects, tenancy base repository | Read in full; live-tested |
| Uploads, tusd hooks, storage, signed URLs | Read in full; live-tested |
| SOW approval and router, Temporal run router, workflow creation | Read; approval path reasoned, not run (needs Temporal) |
| Codegen pre-check, certify/gate stubs | Read; PoC |
| Masters/Filetype import, clamd client, xlsx export | Read the dangerous parts; live-tested (limits, load) |
| Ingest parsers (`ingest/loaders/*`, OCR, rasterize) | Read the PDF loader and limits; PoC on pypdf; other loaders not attacked |
| Compose, Caddy, Dockerfiles, CI workflows, Keycloak realm, Next.js proxy/auth/CSP | Config read; **not attacked live** |
| Dependencies | Scanned (pip-audit, pnpm audit); scanners also: semgrep, bandit |
| Knowledge/Cognee, ISATVON edit APIs, builds router, notifications, most client pages | **Not reviewed in depth** |
| Keycloak, Temporal, tusd, Caddy, Next.js server | **Not part of the live run** |
| LLM prompt injection (documents to SOW to codegen) | **Not tested** |
| Branches | `dev` only (`main` is 459 commits behind). `353-fde-citations` skimmed (N-07). `95-*` are already in `dev`. 33 local-only branches not reviewed. |
| Endpoints | All 79 inventoried (`05`); 21 live-tested, 33 read, 25 listed only |

Scanner results were mostly clean: bandit 1 MEDIUM (false positive: a temp path), semgrep 3 findings (2 false
positives, 1 supply-chain cooldown note). The findings below are mainly architecture and operational gaps, not
injection bugs.

## 2. Summary

| ID | Finding | Evidence | Priority |
|---|---|---|---|
| F-01 | Certify/gate/test/freeze activities are fail-open stubs with no production guard | Code | **P0** |
| N-01 | Parallel calls right after login return HTTP 500 (user-mirror race): 37.5% in repro | **PoC** | **P1** |
| F-02 | Codegen pre-check is a denylist (6 Python bypasses); real gates and sandbox do not exist | **PoC** | **P1** before sandbox ships |
| N-02 | Blocking clamd scan in sync handlers: 45 parallel imports stall the API, health stays green | **PoC** | P2 |
| F-04 | Role demotion not enforced for up to 300 s | **PoC** | P2 |
| F-08 / N-05 | No rate limiting; 125 requests wrote 725 audit rows | **PoC** | P2 |
| F-05 | Invite endpoint is a cross-tenant email oracle | **PoC** | P2 |
| F-07 | Runtime DB role owns tables and can disable immutability triggers | **PoC** | P2 |
| N-06 | Keycloak realm: no password policy, no MFA, no auth events | Code | P2 |
| F-09 | Known default `COGNEE_USER_SECRET` passes validation | Code | P2 |
| F-24 | `pypdf 6.20.1` parses uploaded PDFs in the shared ingest worker; a 14 MB crafted PDF costs 10 s and 523 MB | **PoC** | **P1** |
| F-10 | Other dependency CVEs (1 critical client runtime; otel-api on request path) | Scanner | P2 |
| F-23 | Corrector LLM may patch the FDE-reviewed ISATVON, then the build certifies without re-review | Code | P2 design |
| F-03 | Approval signals Temporal before commit (reverse race, lock across call) | Code | P2 |
| F-06 | No Postgres row-level security (no leak found) | Code + live IDOR | P2 hardening |
| N-07 | FDE citation attribution forgeable (unmerged branch) | Code | block merge |
| F-11 | CSP has no `script-src`; blocklist HTML paste filter | Code | P3 |
| F-12 | Auth path opens 2 sessions per request | Code | P3 |
| F-13 | Keycloak calls inside a DB transaction holding the tenant lock | Code | P3 |
| F-14 | Upload-token key derived from the tusd hook secret | Code | P3 |
| F-15 | Upload slot limit is check-then-act | Unverified | P3 |
| F-16 | Client-declared MIME stored; type by extension until parse stage exists | Code | P3 |
| N-03 | Workbook import materialises all rows (14 MB = 18.5 s, +115 MB) | **PoC** | P3 |
| N-04 | Chunked oversize body answers 400, not 413 | **PoC** | P3 |
| F-17..F-22, N-08 | Hygiene batch (see section 6) | Code/PoC | P3 |

## 3. Critical and high

### F-01 Certification chain is fail-open stubs with no production guard `Code` · P0
`ast_gate` and `static_gate` (`workers/sbam_workers/build/stubs.py:55-80`), `generate_tests`, `build_promptfoo_suite`,
`run_sandbox_tests` (`test/stubs.py`), and `certify_sub_skill`, `certify_macros`, `validate_workflow`,
`certify_workflow`, `freeze_and_version` (`certify/stubs.py`) return success IDs unconditionally;
`freeze_and_version` returns `"1.0.0"`. They register through `@activity_for(...)`. Nothing in `bootstrap.py` or
`registry.py` refuses stubs outside development, whereas the LLM mock does have such a guard
(`Settings._refuse_mock_provider_outside_dev`). `compose.deploy.yaml` runs `worker-build`, `worker-test` and
`worker-certify`, so the stubs would ship.
This is a release blocker for a pre-release product, not an exploitable vulnerability today.
**Impact:** a deployed worker would "certify and freeze" code that was never gated or tested.
**Fix:** register stubs only when `SBAM_ENV` is development/test; otherwise raise a non-retryable `NOT_IMPLEMENTED`.
Add a unit test asserting a production worker serves no stub activity.

### N-01 Parallel requests right after a login return HTTP 500 `PoC` · P1
`SqlUserDirectory._refresh` (`identity/service.py` ~L421) treats a changed `sid` as a new login and updates
`last_login_at`/`last_session_id` through an ORM update guarded by `row_version`. A browser sends several calls at
once after sign-in; each sees "new login", each updates the same row, and all but one raise SQLAlchemy
`StaleDataError` (`persistence.py:949`), which nothing catches, so the response is 500.
**Repro** (`poc/live/bugA.py`): 8 parallel `GET /projects` after each of 15 simulated logins: **45 of 120 failed
(37.5%)**. Smaller bursts fail intermittently (2 of 20).
**Fix:** one atomic statement, `UPDATE users SET last_session_id=:sid, last_login_at=now() WHERE id=:id AND
last_session_id IS DISTINCT FROM :sid`, and write the LOGIN audit row only when `rowcount == 1`. Minimal patch:
catch `StaleDataError` and re-read. Add a concurrency test.

### F-02 Codegen safety is a denylist; the real gates and the sandbox do not exist `PoC` · P1
`build/codegen/bodies.py` blocks `eval/exec/compile/__import__` by name and a few dunder attributes.
`precheck()` returned **no findings** for: `getattr(__builtins__, '__imp'+'ort__')`, `open('/etc/passwd').read()`,
`getattr(getattr((), '__cla'+'ss__'), '__ba'+'se__')`, `globals()`, `for i in range(10**12)`, and
`'a'*(10**11)`, plus three `state_queries` (`SELECT ... FROM sbam.users ...`, `pg_read_file`, `pg_sleep(3600)`).
(`poc/poc_ast_bypass.py` prints a tenth pass, `import sys`, that passes only because the script allowlists `sys`;
ignore it.) The module says the pre-check is "not the authority". On `dev` the authority (AST gate #117, static gate
#119, runtime audit hook #120) is a stub and the sandbox runner is a `sleep` placeholder.
Bodies come from an LLM fed by customer documents, so prompt injection is a realistic path to hostile code.
Which database the `state_queries` run against is **unverified** (the Compose tmpfs Postgres belongs to the
schema-SQL builder; a certified skill's runtime DB is SRIM's, outside this repo), so the SQL impact is unknown.
**Fix:** treat the sandbox as the only boundary (Firecracker/CubeSandbox or gVisor, no network, cgroup limits,
read-only FS, seccomp). Make static checks an allowlist (permitted node types and names), parse SQL with `pglast`
and restrict it to the handoff relation, and run it under a read-only role scoped to one schema.

### N-02 Blocking external calls in sync handlers stall the whole API `PoC` · P2
The masters/filetype import scans the upload with clamd inside a sync handler. With clamd accepting connections but
not answering, one request pinned a worker thread for **120 s** before answering 503 (`SCAN_ATTEMPTS`, backoff,
60 s timeout). **45 parallel imports made `GET /me` by another user time out** (5 s) while `/health/live` stayed
200, so the orchestrator would not restart the process. Any admin can trigger it; so can a clamd outage. The same
pattern applies to Keycloak admin calls (inside a DB transaction, F-13) and the 3 s JWKS fetch.
**Fix:** move the scan into a Temporal activity (the project's own golden rule: long work in activities) or use a
bounded executor with per-endpoint concurrency limits and a total deadline far below the retry budget; make
`/health/ready` reflect thread-pool saturation.

### F-04 Role demotion is not enforced until the token expires `PoC` · P2
`core/auth.py:164` takes roles from the JWT and `admin()`/`fde()` (L340+) authorize on them. The `users` mirror is
consulted only to refuse `DEACTIVATED` accounts. Realm `accessTokenLifespan` is 300 s. **Live:** after setting
`portal_roles` to FDE-only in the DB, the same admin token still got `200` on `GET /admin/projects`.
Deactivation **is** immediate (live: 401 on the next call). The module docstring says role changes "apply at once".
**Fix:** intersect token roles with the DB `portal_roles` in `current_user` (cache 15-30 s keyed by `(sub, sid)`),
and honor Keycloak `not-before`/back-channel logout.

### F-08 / N-05 No rate limiting; audit table can be flooded `PoC` · P2
The only limiter is the knowledge-graph read limiter (`knowledge/graph.py:410`): in-process (per replica), and its
`_hits` dict never evicts users. Nothing limits invites, upload tokens, SOW regenerate or pipeline start (LLM cost).
**Live:** 25 `POST /uploads` requests with 20 rejected files each plus 100 probes of real-looking unassigned
project ids from one FDE added **725 `audit_log` rows in 5.4 s** (~6 per request), unthrottled. The table is
append-only by trigger, so it cannot be pruned without disabling the trigger.
**Fix:** rate limit at Caddy plus a per-tenant/per-user token bucket on LLM-triggering routes (ties into #421 cost
control); aggregate per-file rejection rows into one audit row per request; rate-limit `access_denied` writes.

### F-05 Invite endpoint is a cross-tenant email oracle `PoC` · P2
`identity/service.py:199`: `email_taken()` checks all tenants and answers 409 `USER_EXISTS`. Live: an admin of
tenant A inviting an address that exists in tenant B got `409`; an unknown address went on to the Keycloak step
(`503` here only because Keycloak admin is not configured in the harness; it would be 201). The comment claims the
answer "doesn't say which"; the 409 itself discloses existence anywhere on the platform. It also prevents one person
from belonging to two tenants (possibly a product decision). Needs an admin account to exploit.
**Fix:** per-tenant uniqueness, or the same response either way with the invite delivered by email; rate-limit.

### F-07 The runtime DB role owns the tables, so immutability can be switched off `PoC` · P2
Triggers exist: `audit_log_append_only`, `sow_versions_immutable` (content columns), `*_append_only` on
`master_entry_versions`, `master_snapshots`, `filetype_row_versions`, and the frozen trigger on
`sub_skill_manifests`. But the API connects as `sbam`, which owns every table and the database
(`compose/postgres/init.sh`). **Live:** `ALTER TABLE sbam.audit_log DISABLE TRIGGER audit_log_append_only;
UPDATE sbam.audit_log SET action='TAMPERED'` succeeded. A compromised API process or an SQL injection can erase its
own trail. `workflows`, `code_attempts`, `artifacts` (has `is_frozen`, no trigger) and macro/sub-skill rows have no
immutability trigger. `sow_versions.is_locked` (TD §7.5) is redundant with the content trigger plus `state`.
**Fix:** a separate migration/owner role and a DML-only runtime role (no DDL, no `TRIGGER`), `REVOKE UPDATE, DELETE`
on `audit_log`, triggers on the remaining certified tables, hash-chained audit rows.

### N-06 Keycloak realm: no password policy, no MFA, no authentication events `Code` · P2
`compose/keycloak/realm-bullm.json` has no `passwordPolicy`, no required OTP/WebAuthn action and no `eventsEnabled`,
so login failures are not stored. TD §16 says only "MFA optional (policy flag)". Admins approve SOWs that drive
code generation and certification. Brute-force protection exists (5 failures, temporary lockout up to 15 min);
tokens and sessions are short (300 s / 30 min idle / 10 h max); PKCE S256 on both clients; direct grants off.
**Fix:** `length(12) and notRecentlyUsed and passwordBlacklist`; mandatory TOTP/WebAuthn for `sbam-admin` and
`super-admin`; enable login and admin events and ship them to the audit sink.

### F-09 Known default secret that passes validation `Code` · P2
`compose/compose.yaml:404`: `COGNEE_USER_SECRET: ${COGNEE_USER_SECRET:-change-me-cognee-user-secret-0000000000}`
falls back to a known 38-character value (it derives each project's Cognee password). All `.env.example` secrets are
`change-me-*`. `STORAGE_SIGNING_KEY` is protected by a length check (`signing.py` `MIN_KEY_LENGTH`); the others are not.
**Fix:** `${COGNEE_USER_SECRET:?required}`; extend `scripts/compose_lint.py` to reject `change-me` values and
low-entropy secrets in any deployed env.

### F-24 Crafted PDF exhausts the shared ingest worker (`pypdf 6.20.1`) `PoC` · P1
`ingest/loaders/pdf.py` parses every uploaded PDF with `pypdf.PdfReader`, in-process in `worker-ingest`, after the
ClamAV scan (which does not flag such files). Six of the seven pypdf advisories fixed in 6.21.x are memory or runtime
denial of service from a crafted PDF; the seventh needs Pillow. **Live:** a 14 MB PDF with one 7-million-element
array took **10.3 s and 523 MB RSS** on 6.20.1 (page load only); a 7 MB content stream of 500k tiny operations took
10.7 s and 348 MB. On 6.21.2 both fail immediately with `LimitReachedError`. `poc/pdf/mk.py`, `run.py`.
The ingest queue is shared by all tenants and the worker container has `mem_limit: 1g`, so two such files in
parallel can OOM-kill the worker, and 20 files per batch can monopolise it. Any FDE can upload them (15 MB limit).
Existing `MAX_PDF_PAGES` does not help: the cost is in object parsing, not pages.
**Fix:** `pypdf >= 6.21.2` now (one-line bump, regenerate the lockfile); then run parsers in a separate,
resource-limited process (rlimit/cgroup, wall-clock kill) per document so a poison file cannot take the worker down;
consider per-tenant concurrency limits on the ingest queue.

### F-10 Other dependency vulnerabilities `Code` (scanner output in `poc/`) · P2
- **Client, runtime:** `dompurify` via `monaco-editor` (1 critical SAFE_FOR_TEMPLATES bypass, 1 high; Monaco renders
  LLM-generated code); `path-to-regexp` (high) via `@refinedev/antd`; `qs` (moderate) via refine.
- **Client, dev/test only:** `undici` (6, via jsdom), `zod` (high, via an eslint config).
- **Server:** `pypdf 6.20.1` (see F-24, reachable today); `opentelemetry-api 1.44.0` (`tracestate` parsing on
  the **API request path**); `litellm 1.96.2`; `diskcache 5.6.3` (pickle, no fix, transitive via instructor/cognee);
  `filelock`, `setuptools`, `black`.
**Fix:** bump; run `pip-audit` and `pnpm audit --prod` in CI with an allowlist of accepted exceptions; Renovate or
Dependabot; run parsers in an isolated, resource-limited process.

### F-23 The correction loop can change the approved specification, then certify it `Code` · P2 (design)
`build/codegen/correction.py`: when a gate or test fails, `sbam-corrector` may return an RFC 6902 `isatvon_patch`
on `/isatvon_definition` (other paths are forbidden, and the patched definition must pass ISATVON 1.4 rules and a
loop-spec compile). `SubSkillBuild` then re-manifests from the patched definition (`sub_skill_build.py` docstring:
"a corrector that changed the ISATVON is picked up by the re-manifest") and continues to `certify_sub_skill`
automatically. Tests are generated from the original definition; a model that is rewarded for making tests pass can
loosen thresholds, bounds, expected outcomes or tool contracts instead of fixing code, and the result is certified
with no FDE re-review. The patch and its sha256 are recorded per attempt, and the code viewer (#116) can show
diffs; **I did not verify** whether certification, the SRIM handoff or the UI flags a skill whose ISATVON differs
from the FDE-approved one. The SOW approval hash binds the SOW, not the later ISATVON.
**Fix:** classify patch ops into code-neutral (descriptions, ordering) and behaviour-changing; any behaviour-changing
patch sets `NEEDS_FDE_REVIEW` instead of continuing, and certification records the approved-ISATVON hash and the
final hash. Regenerate the tests from the original definition only.

### F-03 Approval signals Temporal before committing `Code` · P2
`sow/approve.py:253-260` signals (or restarts) the pipeline, then calls `session.commit()`, with the `sows` row held
`FOR UPDATE` (L106) across the network call. `workflows/create.py` re-reads the version in the activity (must be
APPROVED, current, hash-equal; else non-retryable `HashMismatch`), so **no Workflow can be created from an
unapproved SOW**. The remaining defect is the reverse race: the activity can read the pre-commit row and fail
permanently with `NOT_APPROVED` on a legitimate approval (not reproduced; needs Temporal), plus the lock held
across the Temporal call.
**Fix:** commit first, signal from an outbox relay (`notification_outbox` exists) with idempotent retry.

### F-06 No Postgres row-level security `Code` + live IDOR · P2 (hardening, not a leak)
No `ROW LEVEL SECURITY` or `CREATE POLICY` exists (`pg_policies` = 0 on a migrated database; 0 of 36 tables).
Isolation depends on every query using `TenantScopedRepository`. A static sweep of every `select(`/`session.get(`
outside `repository.py` found only tenant-scoped reads, and the live matrix (35 checks across two tenants and
three roles) found no cross-tenant read or write. Soft spots in the base class: `list()` on a project-owned table
without a project in the context returns the whole tenant; `update()` checks tenant, not project; `list()` silently
truncates at `PAGE_LIMIT_MAX`.
**Fix:** non-owner app role, `ENABLE`+`FORCE ROW LEVEL SECURITY`, policies on
`tenant_id = current_setting('app.tenant_id')::uuid`, set with `SET LOCAL` per transaction. Keep the repository as
the first layer.

### N-07 Unmerged branch `353-fde-citations`: citation attribution is forgeable `Code` · block merge
`stamp_fde` (`sow/tiptap.py`) keeps a citation's `author`/`date` when the tuple `(author, date, note)` appears
anywhere in the saved version. The tuple is not bound to the statement. An FDE can read an existing stamp (a
colleague's) from the GET and paste it onto a different statement, keeping the colleague's name. The feature exists
to provide provenance, so this defeats it. Also, an FDE-authored citation satisfies "every statement cites a
source" with no bundle evidence; decide whether that is intended.
**Fix:** bind the stamp to (path, text hash), or sign it with an HMAC over (author, date, note, statement hash).

## 4. Medium and low detail

- **F-11 CSP without `script-src`; blocklist HTML cleaner `Code`.** `client/next.config.ts:10` has only
  `frame-ancestors/object-src/base-uri/form-action` (the comment defers nonces to #51). `cleanPastedHtml`
  (`sow-schema.ts:143`) removes a list of element names, not attributes; TipTap's schema strips most of the rest, so
  exploitability is low but there is no second layer. Fix: nonce CSP in `proxy.ts`; DOMPurify allowlist.
- **F-12 Two sessions per request `Code`.** `current_user` runs a membership query and a mirror sync in separate
  sessions, then the handler opens a third. Steady-state load (80 users x 4 requests) did **not** exhaust the
  default pool (p99 615 ms), so this is wasted work, not a failure. Fix: one session per request, short auth-state cache.
- **F-13 Keycloak writes inside a DB transaction holding the tenant lock `Code`.** `identity/service.py:237-264`:
  lock, up to three HTTP calls, commit. If `set_roles` succeeds and `logout` fails, the DB rolls back but Keycloak
  keeps the new role; a slow Keycloak blocks other admin writes. Fix: outbox/saga plus reconciler.
- **F-14 Upload-token key derived from the tusd hook secret `Code`.** `ingest/tokens.py` derives the signing key
  from the same secret that authenticates tusd to the API, and tusd holds it. Reservations still gate what is
  accepted. Fix: independent secrets; hooks on a private network with mTLS or request signing.
- **F-15 Upload slot limit is check-then-act `Unverified`.** `request_upload` counts then inserts without a lock;
  `pre_create` re-checks, which narrows the window.
- **F-16 Client-declared MIME `Code`.** `is_allowed(name)` is by extension and the stored `mime` is as declared; the
  parse stage does exist (`ingest/detect.py`, `type_detection.py`, `loaders/`), so confirm detection by magic bytes
  decides the loader, not the extension or declared MIME (`Unverified`; not traced).
- **N-03 Workbook import cost `PoC`.** `masters/importer.py` builds `{title: [list(row) ...]}` for all sheets before
  validating structure. A 14.4 MB workbook (400k rows): **18.5 s**, +115 MB RSS, then 422. Three in parallel raised
  another user's `/me` p95 from **9 ms to 547 ms**. Zip-bomb guard works. Fix: validate sheet names and header rows
  from the stream, cap rows per sheet, parse in the worker.
- **N-04 Chunked oversize body `PoC`.** 12 MB sent chunked is refused with `400` ("error parsing the body") instead
  of `413 PAYLOAD_TOO_LARGE`; a declared 11 MB `Content-Length` correctly gives 413. The limit holds; the contract
  does not.

## 5. What is already good (keep it)

RS256 pinned before key lookup and minimum 2048-bit keys; JWKS cooldown against forged `kid`; 404 (never 403) for
other-tenant and unassigned resources; startup refusal of any route without a policy; constant-time hook-secret
comparison; content-addressed storage with a strict key regex; signed-URL name/type bound into the signature;
fail-closed clamd scanning with signature-age checks; zip-bomb limits and macro rejection on xlsx import; formula
injection guards on both exports; HMAC-signed, tenant-bound cursors; strict `limit` parsing; SHA-pinned GitHub
Actions with explicit `permissions:`; digest-pinned base images; non-root containers with healthchecks;
loopback-only published ports; Caddy default-deny routing with Keycloak admin paths blocked; mock-LLM production
guard; Next proxy with path allowlist, Origin check on writes, and header allowlist; open-redirect-safe
`safeCallback`. Live: all token attacks (alg=none, HS256 confusion, wrong aud/iss, expired, foreign key, unknown
kid, malformed claims) and every cross-tenant attempt were refused.

## 6. Hygiene batch

- **F-17** `GET /admin/filetypes` uses the `authenticated` policy: any signed-in user, even without a tenant.
- **F-18** Unpaginated lists (`/admin/tenants`, `/admin/projects`, `/projects`) beside cursor-paginated ones.
- **F-19** `ENABLE_PING` defaults to `true` in `compose.yaml:331` and `SBAM_DEV_AUTH=1` in `.env.example`; deploy
  overlays override both and `compose_lint.py` checks `SBAM_DEV_AUTH`. Add `ENABLE_PING` to the lint.
- **F-20** Signed download links are bearer tokens for up to 15 minutes, not user-bound or single-use. Confirm the
  proxy does not log full URLs (`Unverified`).
- **F-21** `cursor_secret` is random per process; cursors break on restart and across replicas.
- **F-22** Process: `main` is 459 commits behind `dev`; CI on `main` was flagged earlier (#357).
- **N-08** `/api/v1/openapi.json` (72 paths) and `/api/v1/docs` answer without a token on the API itself, and the
  Next proxy forwards them to signed-in users; disable them in production. API JSON carries no
  `Cache-Control: no-store`.
