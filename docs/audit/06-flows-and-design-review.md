# System design and execution flows: review and questioned design choices

Based on reading `dev` @ `d8b0dce` and the design pages (`docs/design/`). Where a stage is a stub, the flow is
described from the code skeleton and design docs, not from observed behaviour. Verdicts: **keep**, **keep +
tighten**, **change**, **unverified**.

## 1. Components

| Component | Role | Notes from the audit |
|---|---|---|
| Next.js (App Router, Refine, Auth.js) | Admin and FDE portals, API proxy with the session token | Tokens stay in an encrypted httpOnly cookie; proxy allowlists path and headers, checks Origin on writes. CSP lacks `script-src` (F-11). |
| FastAPI (`sbam_api`) | REST under `/api/v1`, problem+json, ETag/If-Match, `Idempotency-Key` | Sync handlers on a thread pool (N-02). 79 routes, each with a policy enforced at startup. |
| Keycloak | Identity, tenants as Organizations, roles `super-admin`/`sbam-admin`/`sbam-fde` | No password policy, MFA or events in the realm (N-06). |
| PostgreSQL | Single schema `sbam`, UUIDv7, tenant column on every table | App role owns tables (F-07); no RLS (F-06). |
| Temporal (+ 6 task queues) | `ingest`, `knowledge`, `llm`, `build`, `test`, `certify` | Deterministic workflows with `workflow.patched`; signals never authorize. |
| tusd + ClamAV | Resumable uploads and scanning | Hooks authenticate with a shared secret (F-14). ClamAV does not catch parser-DoS files (F-24). |
| Bifrost (+ OpenRouter) | LLM gateway; all LLM calls use aliases | Mock provider refused outside dev. |
| Cognee | Knowledge graph per project | Per-project password derived from `COGNEE_USER_SECRET` (F-09). |
| CubeSandbox | Test execution for certification | **Not built** (stub). `sandbox-local` is non-certifying. |
| Caddy | Only public service; default-deny routing | HSTS, TLS 1.2+, Keycloak admin paths blocked. No rate limiting (F-08). |

## 2. Execution flow (`ProjectPipeline`, one Temporal workflow per project)

```
FDE uploads -> tusd -> API hook (reservation + token check) -> storage (content-addressed) -> DocumentIngestion
  -> scan (clamd) -> detect type -> parse (loaders, in-process) -> knowledge (Cognee) -> OKF bundle -> generate_sow (LLM)
  -> [wait: FDE approves SOW, hash-bound] -> create_workflow (re-checks approval + hash)
  -> extract macro skills -> extract sub-skills (LLM) -> [wait: generate] -> build_schema_sql
  -> SubSkillBuild children (<= 4 at a time, upstream first):
       manifest -> libraries -> skeleton -> bodies (LLM) -> assemble -> ast_gate* -> static_gate*
       -> tests (LLM) -> promptfoo suite -> sandbox tests* -> certify*      (* = stub on dev)
       failure -> corrector (LLM) -> bodies and/or ISATVON patch -> repeat up to MAX_CORRECTIONS
       -> NEEDS_FDE_REVIEW -> [wait: retry]
  -> certify macros* -> validate workflow* -> certify workflow* -> freeze* -> done
```
What is real on `dev`: upload, scan, type detection and parsing (PDF, Word, xlsx, email, archive, image/OCR loaders), knowledge, SOW generation and approval, Workflow creation, macro/sub-skill
extraction, schema SQL, skeleton and body generation, correction logic, the pipeline and build state machines.
What is a stub: AST and static gates, test generation and sandbox execution, certification and freeze (F-01).

**Where the flow can fail unsafe or surprising**
1. Approval to Workflow: signal before commit (F-03), mitigated by the activity re-check.
2. Correction loop: the LLM can patch the approved ISATVON and the loop then certifies (F-23). Latent until #355 wires the real correction code in.
3. Fail-open stubs would certify in a deployed environment (F-01).
4. Archived projects stay writable through the ISATVON, workflow and build routes (F-29).
5. Parsers run in-process in the shared ingest worker with no per-document resource limits, and pypdf is out of date (F-24).

## 3. Design choices questioned

| # | Choice | Strength | Concern | Verdict and proposal |
|---|---|---|---|---|
| D-01 | Application-layer tenancy only | Simple; 404 for foreign rows; AST guard test | One missed query leaks; DB role owns everything | **Keep + tighten:** RLS and a non-owner role (A-06, A-07). No leak found. |
| D-02 | Roles from JWT claims | Stateless, fast | Demotion lags 300 s; mirror not consulted | **Change:** intersect with DB roles (A-04). |
| D-03 | Sync SQLAlchemy and sync handlers | Simple, fewer async bugs | Blocking external calls pin the 40-thread pool (N-02); extra sessions per request | **Keep + tighten:** bulkheads, one session per request; async only where profiling demands (A-17, A-12). |
| D-04 | Temporal for all long work | Durable, replayable, patched versioning | Approval dual-write (F-03); clamd scan still runs in the API | **Keep:** move remaining long work (scan, import parse) into activities (A-17, A-19). |
| D-05 | Signals are not authorization | Correct | None found | **Keep.** |
| D-06 | Content-addressed storage with strict key regex | Dedupe, integrity, no user-controlled paths | Same sha256 in a project links to an existing version (a deleted one?) `Unverified` | **Keep.** Confirm the dedupe path for soft-deleted documents. |
| D-07 | Signed download URLs (HMAC, 15 min) | No auth round trip for downloads | Bearer links, not user-bound | **Keep + tighten:** shorter TTL or user binding if exports are sensitive (F-20). |
| D-08 | LLM output treated as data, templates + slot bodies | Strong design: model never writes platform fields; `extra=forbid` | Pre-check is a denylist; gates are stubs | **Change:** allowlist gates and a real sandbox (A-02). |
| D-09 | Correction loop may patch ISATVON | Fixes missing libraries and under-specified definitions | Changes the specification after approval to pass tests | **Change:** classify patches, human gate for behaviour changes (A-20). |
| D-10 | SOW approval bound to a content hash | Prevents approve-then-edit | Hash covers the SOW, not later ISATVON | **Keep + extend:** record approved and final ISATVON hashes at certification (A-20). |
| D-11 | Keycloak Organizations as tenants (alias = tenant UUID) | One IdP, no custom tenant auth | Couples tenancy to Keycloak; two-system writes (F-13) | **Keep:** contain with outbox and reconciler (A-13). |
| D-12 | Global unique email | Simple login | Cross-tenant oracle; one person cannot span tenants (F-05) | **Decide:** per-tenant uniqueness or neutral responses (A-05). |
| D-13 | Postgres as the only store (no Redis) | Fewer moving parts | In-process limiter and cursor secret do not scale to replicas (F-08, F-21) | **Keep:** Postgres-backed buckets at the edge or in the app (A-08). |
| D-19 | Untrusted-document parsers run in-process in the shared ingest worker | Simple; limits on pages, entries, unpacked size; scan guard before any parse | No per-document memory/CPU cap; one crafted PDF costs 10 s and 523 MB (F-24) | **Change:** per-document subprocess with rlimits and a deadline; per-tenant ingest concurrency (A-21). |
| D-14 | Local-disk storage backend alongside S3 | Easy local dev | Single-node only | **Keep:** S3-compatible for any multi-replica deployment. |
| D-15 | Idempotency keys on commands, ETag/If-Match on resources | Standard and consistently applied | Not rate limiting; LLM routes can be spammed with new keys | **Keep + add** rate limits (A-08). |
| D-16 | Audit log as an append-only table with triggers | Good baseline, written in the same transaction | Owner role can disable it; write amplification from denials (N-05) | **Change:** role split, aggregation, optional hash chain (A-07, A-08). |
| D-17 | `main` behind `dev` by 459 commits | Fast integration branch | Scanners and newcomers look at stale code; CI gaps on `main` | **Change:** make `dev` the default or release on a cadence (A-15). |
| D-18 | Single FDE can edit and approve a SOW | Matches the product description | No four-eyes | **Unverified need:** ask whether customers require a second approver. |

## 4. Questions for the team (answers change priorities)
1. May one person belong to two tenants? (A-05)
2. Where do `state_queries` run at runtime, and with which role? (F-02 impact)
3. Does certification or the SRIM handoff record the ISATVON hash the FDE approved? (A-20)
4. Is a second approver wanted for SOW approval? (D-18)
5. Which environments will `compose.deploy.yaml` serve before the stubs are replaced? (F-01)
