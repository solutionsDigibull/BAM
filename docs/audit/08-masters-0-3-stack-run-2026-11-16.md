# Masters 0.3 (#781): full Docker stack and end-to-end run, 2026-11-16

This report covers branch `781-masters-0-3-migration`, stacked on #780. It ran on Windows 11 with Docker 29.6.1 and the full `core` Compose stack, built fresh with `up --build`.

## Result

Every Masters check passes, on SQLite, on the real Postgres, through the API and in the browser. The other failures listed below already happen on `dev`; the masters checks either show this branch doesn't cause them or they sit in areas this branch doesn't touch (FDE screens, workers, LLM gateway, sandbox).

| Check | Result |
|---|---|
| Unit, API and contract tests (SQLite), masters-related | 1,263 pass, 1 fails. `test_master_documents::t04` fails on Windows paths and failed before this work |
| ruff, black, contract drift check (`sbam_contracts.gen --check`) | clean |
| mypy --strict | 11 errors. All are Windows-only (`resource.RLIMIT_AS` in ingest isolation) and were there before |
| Client typecheck, and lint of the files this branch changes | clean |
| Client vitest, masters (`src/app/(admin)/admin/masters`) | 51/51 pass when the files run one after another. Run in parallel on this laptop with the stack up, antd renders hit the 5 s timeout |
| Client vitest, everything | 678/681 pass. The 3 failures (FDE Knowledge graph, Build test panel) pass when re-run alone, so they were load |
| Migration 0054 on throwaway Postgres 16: upgrade, downgrade, upgrade | pass. One 0.2 entry per type comes through each step and validates as 0.3 (or as 0.2 after the downgrade) |
| Migration 0054 on the Compose stack (`api-migrate`) | head `0054_masters_0_3` |
| Masters integration tests (`test_masters_db`, `test_master_document*`) | 11/11 pass, including the new `test_mui_2_t06` (0.2 rows migrated, 0.3 rows untouched on Postgres) |
| Playwright E01 logins, E02 cross-tenant, **E03 masters round trip**, E06 rejects, E07 OCR recycle | pass |
| In-depth masters walk on the stack (63 checks, below) | 62 pass. The 63rd is an ordering artefact of the test itself (explained below) |

## In-depth masters walk (real stack, real Keycloak logins)

All 63 checks ran through the web app's `/api/proxy` with real Keycloak sessions for admin-a, admin-b and fde-a1.

- **Master types:** for all 5 masters, the id column (`org_id`, `rule_id`, `agent_id`, `skill_id`, `tooling_id`) comes first. ✓
- **Each of the 5 masters:**
  - a blank id is refused with 422 ✓
  - create returns 201, with row label = stable id = the id ✓
  - the entry reads back ✓
  - an edit creates version 2 ✓
  - a stale `If-Match` is refused (409) ✓
  - a missing `If-Match` returns 428 ✓
- **Status:**
  - a Rules `record_status` of "Active" becomes APPROVED ✓
  - Skills has no status column, so the entry keeps PROPOSED ✓
- **Project scope:**
  - a project's own rule is created ✓
  - it's missing from the tenant list ✓
  - it's in the `?project_id=` list ✓
- **Workbooks:**
  - the template downloads ✓
  - the export downloads ✓
  - the 0.3 fixture imports ✓
  - re-importing the same workbook changes nothing ✓
  - a renamed header is refused with 422 and the diff (`Owner / version` → `Owner`) ✓
- **Export → validate-only re-import:**
  - all five masters tabs are unchanged ✓
  - the Filetype tab reported 43 "created". A fresh stack has no file types until the first workbook import, and this step ran before that import. The later re-import of the same workbook (Filetype included) was a no-op. This is the test's own step order, not a defect.
- **Repo catalog** (FDE, project):
  - reads the Agents `reference` URL ✓
  - reads the Tooling `links_path` URL ✓
  - `structure_version` is 0.3 ✓
- **RBAC / IDOR:**
  - the FDE can't read or write admin masters ✓
  - admin-b gets 404 on tenant A's entry ✓
  - admin-b can't create a version of tenant A's entry (404) ✓
  - admin-b's list has none of tenant A's ids ✓
  - admin-b with tenant A's `project_id` gets 404 ✓
- **UI:**
  - the masters documents list loads ✓
  - the current masters screen shows the 0.3 columns on every tab ✓

  The screen itself is replaced by the approved Admin Masters UI in #783.

## Failures that already happen on `dev`

| Failure | Why it isn't from #781 |
|---|---|
| Alembic drift check (`test_db::test_e1_5_t01`, `migrate check`): `certifications` unique constraint | The constraint is in 0050 and in `certifications/models.py`. This branch changes neither |
| LLM gateway, OCR, Cognee, knowledge build (`test_llm_*`, `test_ocr_*`, `test_cognee_*`, `test_knowledge_build_stack`): Bifrost 502 / `CogneeUnavailable` | No model provider is set up on this laptop. This branch doesn't touch the LLM path |
| Sandbox activity, code bundles, parse stack (`test_e13_2_*`, `test_code_bundles_db`, `test_parse_stack`): `StorageUnavailable` / setup errors | These tests run on the Windows host against storage paths that live inside Docker |
| Keycloak hardening (`test_a18d_*`), `test_stack::t09` | They don't match the current realm export or the readiness payload. Not touched here |
| Playwright E04 start processing, E05 valid mix | The FDE project and documents pages changed on `dev` (#720, #733): "Processing is running." and the documents rows aren't there any more. This branch changes no FDE screen and no worker |
| Every stack E2E login | The admin login button is now "Sign in" (#733), and the helper looked for "Sign in with Keycloak". **Fixed here** in `client/e2e/stack/stack.ts`: it accepts both |

## Fixed while testing

- **Migration 0054 re-upgrade:** after a downgrade, entries created natively in 0.3 would have been re-mapped as if they were 0.2. `upgrade()` now skips any payload that already has its id key (no 0.2 model has one). Covered by `test_mui_2_t06` on Postgres.
- **Wording:** the masters screen and the import wizard said "BAM 0.2 masters". They now say Masters 0.3.
- **Integration test:** `test_masters_db.py` now uses 0.3 payloads.
