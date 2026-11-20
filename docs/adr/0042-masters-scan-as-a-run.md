# 0042 — The masters scan and import run in Temporal; the request answers 202 when the client asks

- Status: Accepted
- Date: 2026-11-20
- Related: issue #609 (N-02b, audit option (a)), refines [0029](0029-approved-masters-ui.md); PRs #660, #672, #673, #874 (the other slices)

## Context

`POST /admin/masters/import` and `POST /admin/master-documents` scanned the file with clamd and parsed it
inside the request. A hung clamd held one of the API's request threads for up to the scan deadline, and the
clamd bulkhead (#609) only capped how many threads it could take. The platform rule is that API handlers never
block: long work runs in Temporal activities, and a long operation answers 202 with a run object.

## Decision

1. **A run, not a stage.** `MastersScanImport` (workflow id `masters-scan-<scan_id>`, queue `sbam-ingest`) with the
   activity `scan_and_import_masters`, in `sbam_workers/ingest/masters_scan*.py`. It is not a stage of
   ProjectPipeline. The activity calls the same `importer.import_workbook` and `documents.attach` the
   synchronous routes call, so the result, the refusals and the audit rows are the ones of today. A document
   that ends clean and unparsed starts `MasterDocumentIngestion` from the workflow, as the attach route did.
2. **The request keeps only the cheap checks** (role, tenant, project, size, type, `Idempotency-Key`), stores the
   bytes unscanned in the scan's own storage scope (`<tenant>/<project>/<scan_id>/uploads/<sha256>`, which
   nothing else references or serves), writes the audit row `MASTERS_SCAN_ACCEPTED` and starts the run. No
   clamd call, no bulkhead slot.
3. **No new table, no migration.** `scan_id` = uuid5(tenant, `Idempotency-Key`): the same key finds the same scan
   and run (another file under the same key is 422 `IDEMPOTENCY_KEY_REUSED`). The outcome is one audit row
   (`MASTERS_SCAN_DONE` or `MASTERS_SCAN_FAILED`, unique per scan); QUEUED / RUNNING come from the existing
   `temporal_runs` mirror; an import's `ImportReport` is a JSON file in the scan's scope.
4. **Status:** `GET /admin/masters/scans/{scan_id}` returns `MastersScanStatus` (tenant-scoped; another tenant's
   id is 404). FAILED carries a `ProblemDetail` with the code the synchronous route would have answered.
5. **Failures.** What the file earns (`MALWARE`, `STRUCTURE_MISMATCH`, `UNSUPPORTED_TYPE`, ...) ends the scan FAILED
   once and is never retried. A 503 (clamd down, bulkhead full, storage unavailable) is retried by Temporal with
   backoff; after the last attempt the scan is FAILED with `SCAN_UNAVAILABLE` and nothing is imported. Temporal
   unreachable at accept time is 503 `ORCHESTRATOR_UNAVAILABLE`: the stored bytes are removed
   (`MASTERS_SCAN_ABANDONED`) and the same key starts afresh.

## The old 201 behaviour (recommended option taken)

The 202 answer is **opt-in per request**: `Prefer: respond-async` (RFC 7240, answered with
`Preference-Applied: respond-async`) plus an `Idempotency-Key`. Without `Prefer` the routes answer exactly as
before (200 report, 201/200 document). Reason: the Admin masters screens (#783 is still porting them) cannot poll
yet, and a hard switch would break the import wizard on `dev` between the backend and client PRs. The synchronous
path still calls clamd inside the request (under the bulkhead of #874), so it is **deprecated**: it is removed,
and the 202 made the only answer, in the PR that makes the client poll (step 3 of #609 or a follow-up once #783
has merged).

## Consequences

- The client sends `Prefer: respond-async` and polls `GET /admin/masters/scans/{scan_id}` until DONE or FAILED.
- The worker's clamd settings are the worker's own (`CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT_SECONDS`).
- A retried activity after the import committed but before the outcome was stored reruns an idempotent upsert; its
  report then counts those rows as `unchanged`. The window is one database round trip.
- Report files stay in storage under the scan's scope (a few KB each); a retention sweep is a follow-up.
