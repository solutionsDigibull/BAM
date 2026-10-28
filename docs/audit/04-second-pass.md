# Second pass: challenge of the first audit, live pentest, new findings

Target and method: `origin/dev` at `d8b0dce`. This pass tried to disprove each first-pass finding and then
attacked a **live API** built from this tree: uvicorn on a throwaway Postgres 16 (own container, own port), a local
JWKS and RSA key to mint tokens, the repo's own fake clamd. The existing SBAM stack on the workstation was not
touched. Scripts are in `poc/live/` (the private key is not committed; `lib.py` regenerates one).
Not done even now: Keycloak, Temporal, tusd, Caddy and the Next.js server were not part of the live run (their
config was read, not attacked); LLM prompt injection was not tested; Cognee/knowledge, ISATVON edit APIs and the
builds router got no deep review.

## 1. What I got wrong or overstated in the first pass

| First pass | What the challenge found | Now |
|---|---|---|
| **F-07** "audit_log has no trigger / append-only not DB-enforced" | **Wrong.** `audit_log` has an `audit_log_append_only` trigger. So do `master_entry_versions`, `master_snapshots`, `filetype_row_versions`; `sow_versions` has a content-immutability trigger; `sub_skill_manifests` has the frozen trigger. My grep missed them. | Reframed: the triggers exist, but the runtime role `sbam` **owns** the tables. Live: `ALTER TABLE sbam.audit_log DISABLE TRIGGER ...` then `UPDATE` succeeded. A compromised API process or SQL injection can erase its own trail. Also `workflows`, `code_attempts`, `artifacts` (has `is_frozen`, no trigger) and macro/sub-skill rows have no trigger. |
| **F-03** "approval can create a Workflow for an unapproved SOW if the commit fails" | **Overstated.** `workflows/create.py` re-reads the version in the activity: it must still be APPROVED, be the SOW's approved version, and hash-match, else a non-retryable `HashMismatch`. No phantom Workflow can be made. | The real defect is the opposite race: the signal is sent *before* the commit, so the activity can read the pre-commit row and fail permanently with `NOT_APPROVED` on a legitimate approval. Not reproduced (needs Temporal). Downgraded to P2, `Live?`. The lock-held-across-network-call point stands. |
| **F-04** "demotion effective only at token expiry (unbounded)" | Bounded: realm `accessTokenLifespan` is 300 s. Deactivation **is** immediate (live: deactivated FDE got 401 on the next call). | **Confirmed live** for role demotion: after setting the DB `portal_roles` to FDE-only, the same admin token still got `200` on `/admin/projects`. Window is up to 5 minutes. P2. |
| **F-02** "9 payloads pass" | The count was right (6 Python escapes + 3 SQL queries). The script prints a 10th pass (`import sys`) that only passes because I put `sys` on the allowlist; ignore it. I also implied the SQL queries reach SBAM's own tables. I have **not** verified where `state_queries` run: the Compose Postgres on an internal tmpfs network belongs to the schema-SQL builder, and a certified skill's runtime DB is SRIM's, outside this repo. | Python escapes stand (and matter once a sandbox exists). The SQL part is downgraded to "unrestricted SELECT in whatever DB the skill is given", impact unverified. |
| **F-05** P1 | Needs an authenticated tenant admin, and the oracle is "does this email exist anywhere". | Confirmed live (409 vs not-409). Downgraded to P2. |
| **F-12** "pool exhaustion under 40 threads x 15 connections" | Steady state did **not** reproduce: 80 concurrent users x 4 requests gave 320 x 200, p99 615 ms. | Pool starvation is real only when requests hold connections for a long time (see N-05). The per-request double session is still wasteful but not a failure. Downgraded to P3. |
| **F-06** RLS | No cross-tenant leak found. A static sweep of every `select(`/`session.get(` outside `repository.py` found only tenant-scoped or tenant-row reads; live IDOR matrix (35 checks) passed. | Kept as defense-in-depth hardening, not a vulnerability. |
| **F-01** | Stands, but it is a release blocker for a pre-release product, not a vulnerability. `compose.deploy.yaml` does run `worker-build/test/certify`, so the stubs would ship. | Unchanged. |

## 2. Live results: what held

About 90 checks across two scripts. All of these held:
alg=none, HS256 key confusion, wrong `aud`/`iss`, expired, attacker-signed with the right `kid`, unknown `kid`,
two-org tenant claim, `roles` as a string; cross-tenant and unassigned access answers 404 on every route tried
(documents, project, members, PATCH, PUT members, runs); `adminA` cannot add a tenant-B user to a project (422);
another tenant's cursor and edited cursors are rejected; tusd hook secret (missing, wrong, empty), forged and
cross-project upload tokens, wrong filename or size, deferred size, replay of a used reservation, traversal ids,
double extensions and the 15 MB + 1 limit; signed URLs (tampered, expired, other tenant's key, unsigned `name`,
traversal) with no existence oracle; `limit=` fuzzing (0, -1, 1e6, 1e3, non-numeric, 30 digits) answers 400;
audit filters reject injection; no CORS headers for a foreign `Origin`; JWKS and token handling never 500.
The redirect guard `safeCallback` and the Next proxy (path allowlist, Origin check, header allowlist) read as sound.

## 3. New findings

### N-01 Parallel requests right after a login return HTTP 500 `PoC` · P1
`SqlUserDirectory._refresh` (`identity/service.py`, around line 421) compares `last_session_id` to the token's
`sid` and, when it differs, writes `last_login_at`/`last_session_id` through an ORM update guarded by `row_version`.
A browser fires several API calls at once after sign-in; each one sees "new login", each updates the same row, and
all but one hit `StaleDataError` (SQLAlchemy `persistence.py:949`), which is not caught and becomes a 500.
Repro (`poc/live/bugA.py`): 8 parallel `GET /projects` after each of 15 simulated logins gave **45 failures in 120
requests (37.5%)**. Smaller bursts fail intermittently (2 of 20).
**Fix:** record the session with one atomic statement (`UPDATE users SET last_session_id=:sid, last_login_at=now()
WHERE id=:id AND last_session_id IS DISTINCT FROM :sid`) and write the LOGIN audit row only when `rowcount == 1`.
Catching `StaleDataError` and re-reading is the minimal patch. Add a concurrency test.

### N-02 Sync handlers + blocking calls to external services = API-wide stall `PoC` · P2
The masters/filetype import scans the upload with clamd inside a sync handler. With clamd accepting connections but
not answering, one request pins a worker thread for the retry budget (a single request ran **120 s** before
answering 503). **45 parallel imports against a hung clamd made `GET /me` by another user time out** (5 s), while
`/health/live` stayed 200, so the orchestrator would not restart it. Any admin can trigger this, and so can a
plain clamd outage. The same pattern applies to Keycloak admin calls and the 3 s JWKS fetch.
**Fix:** run the scan as a Temporal activity (the golden rule already says long work belongs there) or at least in
a bounded executor with a per-endpoint concurrency limit (bulkhead) and a total deadline well under the retry
budget; make `/health/ready` reflect thread-pool saturation.

### N-03 Workbook import reads every row of every sheet into memory before validating `PoC` · P3
`masters/importer.py` builds `{title: [list(row) for row in ws.iter_rows()]}` for all worksheets, then checks
structure. A 14.4 MB workbook (400k rows) cost **18.5 s** and +115 MB RSS, then was rejected as "not the BAM 0.2
structure". Three in parallel raised other users' `/me` p95 from **9 ms to 547 ms** (max 1 s). No outage, so P3,
but it is an admin-triggered CPU amplifier. The zip-bomb guard works as designed.
**Fix:** check sheet names and the first rows from the stream, cap rows per sheet (the template has a fixed size),
and move the parse into the worker.

### N-04 Chunked oversize body answers 400 instead of 413 `PoC` · P3
A 12 MB body sent with chunked transfer encoding (no `Content-Length`) is refused, but as `400` ("error parsing
the body"). The `BodyLimitMiddleware` raises inside `receive`, and FastAPI converts it. The limit is enforced; the
contract (`PAYLOAD_TOO_LARGE`, 413) is not. A declared `Content-Length` of 11 MB correctly returns 413.

### N-05 Audit flooding is measured, not theoretical `PoC` · P2 (was F-08)
25 `POST /uploads` requests with 20 rejected files each, plus 100 probes of real-looking unassigned project ids
from one FDE, added **725 `audit_log` rows in 5.4 s** (about 6 rows per request) with no throttling. Because the
rows are append-only (trigger), an attacker can grow the table and the DB cannot be pruned without disabling the
trigger. Needs per-user rate limiting and aggregation of rejection rows.

### N-06 Keycloak realm: no password policy, no MFA, no authentication events `Code` · P2
`compose/keycloak/realm-bullm.json` has no `passwordPolicy`, no required OTP/WebAuthn action, and no
`eventsEnabled` (login failures are not stored). TD §16 says only "MFA optional (policy flag)". Admins approve SOWs
that drive code generation and certification. Brute-force protection exists (5 failures, up to 15 min lockout,
temporary only) and tokens/sessions are sensibly short.
**Fix:** `length(12) and notRecentlyUsed and passwordBlacklist`, mandatory TOTP/WebAuthn for `sbam-admin` and
`super-admin`, enable login and admin events and ship them to the audit sink.

### N-07 Unmerged branch `353-fde-citations`: FDE citation attribution is forgeable `Code` · pre-merge review
`stamp_fde` (`sow/tiptap.py`) keeps a citation's `author`/`date` when the tuple `(author, date, note)` already
appears anywhere in the saved version. The tuple is not bound to the statement it supports. An FDE can read an
existing stamp (for example a colleague's) via the GET and paste the same triple onto a different, new statement,
which keeps the colleague's name. The feature exists to give provenance, so this breaks it. Also, an FDE-authored
citation satisfies the "every statement must cite" rule without any bundle evidence; decide whether that is intended
and cap or flag such statements in review and approval.
**Fix:** bind the stamp to (path, text hash) or sign it with an HMAC over (author, date, note, statement hash).
`95-sow-new-version` and `95-scope-change-carry-over` are already contained in `dev` (the same feature landed as
#408); they need no review.

### N-08 Smaller observations `Code`/`PoC`
- `GET /api/v1/openapi.json` (72 paths, 139 KB) and `/api/v1/docs` answer 200 without a token on the API itself. The
  Next proxy forwards any `/api/v1/*` path for a signed-in user, including these. Reachable only through the
  portal origin in production, so informational; disable them when `SBAM_ENV` is production.
- Authenticated JSON has no `Cache-Control: no-store` from the API (the proxy forwards the header only if present).
- The clamd-reply path wraps a `ClamdError` into a generic 503; good. The fake-clamd `INSTREAM` path was exercised.

## 4. Updated severity table

| ID | Finding | Evidence | Priority |
|---|---|---|---|
| F-01 | Fail-open certify/gate stubs | Code | P0 release blocker |
| N-01 | 500s after login (race) | **PoC** 45/120 | P1 |
| F-02 | Codegen denylist (Python part) + missing real gates | **PoC** | P1 (before sandbox ships) |
| N-02 | Thread-pool stall via blocking scan | **PoC** | P2 |
| F-04 | Role demotion lag up to 5 min | **PoC** | P2 |
| N-05 / F-08 | Audit flooding, no rate limits | **PoC** +725 rows | P2 |
| F-05 | Cross-tenant email oracle | **PoC** | P2 |
| F-07' | App role owns tables, can disable triggers | **PoC** | P2 |
| N-06 | Keycloak: no password policy/MFA/events | Code | P2 |
| F-09 | Known default `COGNEE_USER_SECRET` | Code | P2 |
| F-10 | Dependency CVEs (1 critical, client runtime) | scanner | P2 |
| F-03' | Approval signal-before-commit race | Code | P2 |
| F-06 | No RLS (no leak found) | Code + live IDOR | P2 hardening |
| N-07 | Citation attribution forgeable (unmerged) | Code | block merge |
| F-11, F-14, N-03, N-04, F-12' | CSP, key reuse, import cost, 413 vs 400, pool | PoC/Code | P3 |
