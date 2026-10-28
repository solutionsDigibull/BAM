# SBAM security and design audit: findings

Target: `origin/dev` at `d8b0dce` (2026-10-28). Method: manual code review plus semgrep, bandit,
pip-audit, pnpm audit, and one executable PoC (`poc/poc_ast_bypass.py`). Raw scanner output is in `poc/`.

**Confidence tags.** `PoC` = I ran it and it reproduced. `Code` = confirmed by reading the cited lines, not run
against a live stack. `Live?` = plausible from the code, needs a running stack to confirm.

## Coverage: what was and was not examined

| Area | Depth |
|---|---|
| Auth chain (`core/auth.py`), identity, projects, tenancy base repo | Read in full |
| Uploads (`ingest/`), tusd hooks, storage + signed URLs | Read in full |
| SOW approve, SOW router, Temporal run router | Read in full / mostly |
| Codegen pre-check (`build/codegen/bodies.py`), stubs, certify chain | Read, PoC run |
| Masters/Filetype import, xlsx export, clamd client | Skimmed for the dangerous parts only |
| Compose, Caddy, Dockerfiles, CI workflows, Next.js config/proxy/auth | Read for security-relevant parts |
| Dependencies | Scanned (pip-audit, pnpm audit) |
| Knowledge/Cognee, ISATVON edit APIs, builds router, notifications, most of the client UI | **Not reviewed in depth** |
| Live pentest of a running stack (`make up`), fuzzing tusd/Keycloak/Temporal | **Not done** |
| LLM prompt-injection testing (documents to SOW to codegen) | **Not done** |
| Branches | `dev` only. `main` is 459 commits behind. 3 unmerged branches (`95-sow-new-version`, `95-scope-change-carry-over`, `353-fde-citations`) and 33 local-only branches not reviewed. |

Scanner results were mostly clean (bandit: 1 MEDIUM, a false positive on a temp path; semgrep: 3 findings,
2 false positives). The team has done real hardening work. Section "What is already good" lists it.
The findings below are therefore mostly architectural gaps, not sloppy injection bugs.

---

## Critical / High

### F-01 Certification chain is fail-open stubs with no production guard `Code` · P0 before any go-live
`ast_gate`, `static_gate` (`workers/sbam_workers/build/stubs.py:55-80`), `generate_tests`, `run_sandbox_tests`
(`test/stubs.py`), `certify_sub_skill`, `certify_workflow`, `freeze_and_version` (`certify/stubs.py`) return
success IDs unconditionally; `freeze_and_version` returns version `"1.0.0"`. They are registered by
`@activity_for(...)`, and nothing in `bootstrap.py` or `registry.py` refuses stubs outside development. The LLM
mock has exactly such a guard (`Settings._refuse_mock_provider_outside_dev`); the stubs do not.
**Impact:** a staging/production worker would "certify and freeze" code that was never gated or tested, and
SRIM would trust it. This breaks the product's core promise.
**Fix:** stubs register only when `SBAM_ENV` is dev/test. Otherwise they raise a non-retryable
`NOT_IMPLEMENTED`, and a unit test asserts the production worker serves no stub activity.

### F-02 Codegen pre-check is a denylist and is trivially bypassed; the real gates do not exist `PoC`
`build/codegen/bodies.py` blocks `eval/exec/compile/__import__` by name and a few dunder attributes. Running
`precheck` on these bodies returns **no findings** (see `poc/poc_ast_bypass.py`):
`getattr(__builtins__, '__imp'+'ort__')`, `open('/etc/passwd').read()`, `getattr(getattr((),'__cla'+'ss__'),
'__ba'+'se__')`, `globals()`, `for i in range(10**12)`, `'a'*(10**11)`. The `state_queries` check is
`startswith("SELECT")` + one `%s` + no `;`, so `SELECT email FROM sbam.users WHERE %s IS NOT NULL`,
`SELECT pg_read_file(...)` and `SELECT pg_sleep(3600)` all pass.
The docstring says the pre-check "is not the authority" and defers to the AST gate (#117), static gate (#119) and
runtime audit hook (#120). On `dev` all three are stubs (F-01) and the sandbox runner is a `sleep` placeholder
(`compose.sandbox-local.yaml`). Bodies come from an LLM fed by customer documents, so prompt injection is a
realistic way to get hostile code into the pipeline.
**Fix:** treat the sandbox as the only boundary (CubeSandbox/Firecracker or gVisor, no network, cgroup limits,
read-only FS, seccomp). Make static checks an allowlist (permitted node types and names), parse SQL with `pglast`
and restrict it to the handoff relation, and run it under a read-only DB role scoped to one schema.

### F-03 SOW approval signals Temporal before the DB commit `Code` · P1
`sow/approve.py:253-260` calls `signal_pipeline`/`restart_pipeline` and only then `session.commit()`, with the
`sows` row lock (`for_update=True`, line 106) held across the network call. If the commit fails after the signal
is accepted, the pipeline builds a Workflow from a SOW that is not approved in the database. If Temporal is slow,
the lock is held for the whole call. The code comment frames this as "nothing approved on failure", but that holds
only for the signal failing, not the commit.
**Fix:** transactional outbox (`notification_outbox` already exists): commit the approval and an outbox row, then
signal from a relay with idempotent retry. Also have the Workflow-creation activity re-read the approved version
from the DB and refuse otherwise.

### F-04 Role changes and demotions do not take effect until the token expires `Code` · P1
`core/auth.py:164` takes roles from the JWT and `admin()/fde()` (line 340+) authorize on them. The `users` mirror
is consulted only to refuse `DEACTIVATED` accounts (`identity/service.py`). `update_user` calls `users.logout()`
(service.py:264), but an access token that is already issued still verifies (no introspection, no `not-before`
check), so a demoted admin keeps admin rights for the access-token lifetime. The module docstring says changes
"apply at once". `portal_roles` in the DB is not enforced on requests.
**Fix:** intersect token roles with the DB `portal_roles` in `current_user` (cached 15-30 s by `(sub, sid)`),
keep access tokens at 5 minutes or less, and honor Keycloak's `not-before`/back-channel logout.

### F-05 Cross-tenant email oracle on user invite `Code` · P1
`identity/service.py:199`: `email_taken(session, body.email)` checks all tenants and answers 409 `USER_EXISTS`.
The comment says the answer does not say which tenant, but the 409 itself tells any admin whether an address exists
anywhere on the platform. It also blocks one person from being in two tenants, which may be a product decision.
**Fix:** make uniqueness per tenant, or return the same 201/202 response either way and send the invite email
out of band. Rate-limit the endpoint.

### F-06 Tenant isolation exists only in application code; no Row-Level Security `Code` · P1
No `ROW LEVEL SECURITY` or `CREATE POLICY` anywhere in migrations or compose. The API connects as role `sbam`,
which owns the tables. Everything depends on every query going through `TenantScopedRepository`
(`db/repository.py`). Specific soft spots there: `list()` on a project-owned table with no project in the context
returns the whole tenant (`_scoped`, project filter is conditional); `update()`/`_check_owned()` check tenant
only, not project; `list()` silently truncates at `PAGE_LIMIT_MAX`. An AST guard test (#358) catches only inline
tenant-only contexts. One forgotten `select(Model)` is a cross-tenant leak.
**Fix:** non-owner app role, `ENABLE`+`FORCE ROW LEVEL SECURITY`, policies on `tenant_id =
current_setting('app.tenant_id')::uuid`, set with `SET LOCAL` per transaction. Keep the repository as the first
layer.

### F-07 "Frozen/immutable" and "append-only" are not enforced by the database `Code` · P1
`create_frozen_trigger` is applied to one table only (`sub_skill_manifests`, migration 0033). `workflow_versions`,
`sow_versions`, certificates and `audit_log` have no trigger. `audit_log` is described as append-only
(migration 0003 docstring) but has no trigger and no `REVOKE UPDATE, DELETE`, and the app role owns it.
`sow_versions.is_locked` is documented in TD §7.5 but was never implemented. (An uncommitted attempt exists in the
original working tree: no backfill of existing approved rows, and no test.)
**Fix:** triggers on every frozen table; `REVOKE UPDATE, DELETE ON audit_log` from the app role; hash-chain
audit rows; backfill `is_locked` for APPROVED/SUPERSEDED rows in the same migration.

### F-08 No general rate limiting; audit-table flooding `Code` · P1
The only limiter is the knowledge-graph read limiter (`knowledge/graph.py:410`): in-process (per replica) and its
`_hits` dict never evicts users. Nothing limits login-adjacent, invite, upload-token, SOW-regenerate or
pipeline-start calls. Each rejected file in `POST /uploads` writes an audit row (`ingest/service.py:145`, up to
20 per request), and each denial writes another, so an authenticated FDE can grow `audit_log` without bound.
LLM-cost endpoints are protected only by idempotency keys.
**Fix:** limit at Caddy, plus a per-tenant/per-user token bucket for LLM-triggering routes tied to the #421
cost-control work. Aggregate rejection audit rows per request.

### F-09 Default secrets that pass validation `Code`
`compose/compose.yaml:404`: `COGNEE_USER_SECRET: ${COGNEE_USER_SECRET:-change-me-cognee-user-secret-0000000000}`
falls back to a known 38-character value. It derives each project's Cognee password. The `.env.example` values
are all `change-me-*`. Compare `STORAGE_SIGNING_KEY`, which does reject short placeholders (`signing.py`
`MIN_KEY_LENGTH`).
**Fix:** `${COGNEE_USER_SECRET:?required}`; extend `scripts/compose_lint.py` to reject `change-me` values and
low-entropy secrets for any non-dev env.

### F-10 Dependency vulnerabilities `Code` (scanner output in `poc/`)
- **Client, runtime:** `dompurify` via `monaco-editor`: 1 critical (SAFE_FOR_TEMPLATES bypass) + 1 high. Monaco
  renders LLM-generated code. `path-to-regexp` (high) via `@refinedev/antd`; `qs` moderate via refine.
- **Client, dev/test only:** `undici` (6 findings, via jsdom), `zod` (high, via eslint config).
- **Server:** `pypdf 6.20.1`: 7 CVEs fixed in 6.21.x (crafted-PDF DoS/RCE class). PDF parsing is not implemented yet
  (declared in `pyproject.toml`, unused), but it will parse untrusted uploads. `opentelemetry-api 1.44.0`
  (`tracestate` header parsing on the **API request path**), `litellm 1.96.2`, `diskcache 5.6.3` (pickle, no fix,
  transitive), `filelock`, `setuptools`, `black`.
**Fix:** bump; run `pip-audit` and `pnpm audit --prod` in CI with allowlisted exceptions; Renovate/Dependabot;
run parsing in an isolated, resource-limited process.

---

## Medium

### F-11 Content-Security-Policy has no `script-src`/`default-src` `Code`
`client/next.config.ts:10` ships only `frame-ancestors/object-src/base-uri/form-action`. The comment defers
nonces to #51. The SOW editor's paste filter (`sow-schema.ts:143 cleanPastedHtml`) is a blocklist of element
names, so attributes like `onmouseover` or `javascript:` hrefs on allowed elements are not removed. TipTap's
schema strips most of it afterwards, so exploitability is low, but there is no second layer.
**Fix:** nonce-based CSP set in `proxy.ts`; replace the blocklist with DOMPurify and an allowlist.

### F-12 Per-request cost of authentication and a small DB pool `Code`/`Live?`
`current_user` runs, per request, a JWKS lookup (3 s timeout, blocking), a membership query in one session
(`SqlMemberships`) and a user-mirror sync in a second session (`SqlUserDirectory.sync`), then the handler opens a
third. Handlers are sync (threadpool) on SQLAlchemy sync; `make_engine` sets no `pool_size`/`max_overflow`, so
SQLAlchemy's default (5+10) applies against Starlette's 40 worker threads. Under load this shows up as
`QueuePool limit` timeouts. Needs a load test to confirm.
**Fix:** one session per request via a dependency; cache `(sub, sid)` auth state for seconds; size the pool
explicitly; consider async only where I/O-heavy.

### F-13 Keycloak calls inside a DB transaction that holds a tenant lock, with partial-failure drift `Code`
`identity/service.py:237-264`: `lock_tenant`, then up to three HTTP calls to Keycloak, then commit. If `set_roles`
succeeds and `logout` fails, the DB rolls back but Keycloak keeps the new role. A slow Keycloak holds the tenant
lock and blocks other admin writes.
**Fix:** outbox/saga with a reconcile job, or order the writes so the DB is the source of truth and Keycloak is
converged afterwards.

### F-14 Upload token key derived from the tusd hook secret `Code`
`ingest/tokens.py`: the token-signing key is `HMAC(hook_secret, "sbam-upload-token-v1")`. The same secret
authenticates tusd to the API (`ingest/router.py:hook_secret`), and tusd holds it. Anyone who gets that secret
can both call hooks and mint tokens. Reservations still gate what is accepted, which limits impact.
**Fix:** two independent secrets; protect hooks on a private network with mTLS or a per-request signature.

### F-15 Upload slot limit is check-then-act without a lock `Live?`
`ingest/service.py request_upload` counts active + pending, then inserts; concurrent requests can exceed
`MAX_ACTIVE_FILES`. `pre_create` re-checks, which narrows the window.

### F-16 Client-declared MIME stored; type decided by extension `Code`
`is_allowed(name)` is extension-based and `mime` is stored as declared. Sniffing is deferred to the parse stage
that is not implemented (`docs/design/07` says so). Make sure the parse stage validates magic bytes before any
parser runs.

---

## Low / hygiene

- **F-17** `GET /admin/filetypes` uses the `authenticated` policy (`filetypes/router.py:56`): any signed-in user,
  including a user with no tenant or portal role.
- **F-18** Several list endpoints are unpaginated (`/admin/tenants`, `/admin/projects`, `/projects`) while others use
  cursors; clients cannot rely on one contract.
- **F-19** `ENABLE_PING` defaults to `true` in `compose.yaml:331` and `SBAM_DEV_AUTH=1` in `.env.example`.
  Deploy overlays correctly override both and `compose_lint.py` checks `SBAM_DEV_AUTH`; add the same lint for
  `ENABLE_PING`.
- **F-20** Signed download links are bearer tokens for up to 15 minutes, not bound to a user or single-use
  (`storage/signing.py`). Acceptable, but log redaction at the proxy should be confirmed `Live?`.
- **F-21** `Settings.cursor_secret` is random per process; cursors break on restart and across replicas (the code
  comment already notes this). Make it required when more than one replica runs.
- **F-22** Process: the default branch `main` is 459 commits behind `dev`; CI on `main` has been flagged before
  (#357). Contributors and scanners looking at `main` see an old codebase.

---

## What is already good (kept, not to be "improved" away)

RS256 pinned before key lookup, minimum 2048-bit keys, JWKS cooldown against forged `kid`, 404 for other-tenant
and unassigned resources, startup refusal of any route without an authorization policy, constant-time hook-secret
comparison, content-addressed storage with a strict key regex, signed-URL name/type bound into the signature,
clamd fail-closed scanning with signature-age checks, zip-bomb limits and macro rejection on xlsx import, formula
injection guards on both xlsx exports, SHA-pinned GitHub Actions with explicit `permissions:`, digest-pinned base
images, non-root containers with healthchecks, loopback-only published ports, Caddy default-deny routing, and a
`mock LLM` production guard.
