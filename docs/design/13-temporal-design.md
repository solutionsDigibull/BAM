> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §13. Temporal design


## 13.1 Namespaces, queues, IDs

| Item              | Value                                                                                              |
| ----------------- | -------------------------------------------------------------------------------------------------- |
| Namespace         | `sbam-local`, `sbam-dev`, `sbam-staging`, `sbam-prod`                                      |
| Task queues       | `sbam-ingest`, `sbam-knowledge`, `sbam-llm`, `sbam-build`, `sbam-test`, `sbam-certify` |
| Workflow IDs      | `pipeline-<project_id>`, `ingest-<document_version_id>`, `build-<sub_skill_id>-<build_no>`  |
| Payloads          | IDs and small DTOs only; content in storage/DB                                                     |
| Search attributes | `TenantId`, `ProjectId`, `SbamWorkflowId`, `SkillId`, `SbamStage`                        |

## 13.2 Temporal workflows

| Temporal workflow                            | Steps                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Human waits (signals)                                             |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `ProjectPipeline` (one per project) | wait documents → child`DocumentIngestion` per file → `build_knowledge` → `build_okf_bundle` → `generate_sow` → **wait `sow_approved`** → `create_workflow` (SBAM creates the Workflow for the approved SOW version) → `extract_macro_skills` → `extract_sub_skills` (parallel per macro) → **wait `generate`** → `build_schema_sql` → `plan_builds` → child `SubSkillBuild` per Sub-Skill (parallel, limit `build.max_parallel` 1–16, default 4; upstream Sub-Skills certified first) → `certify_macros` → `validate_workflow` → `certify_workflow` → `freeze_and_version` → notify | `sow_approved`, `generate`, `rebuild_subskills`, `cancel` |
| `DocumentIngestion`                        | `scan_clamav` → `detect_type` (Filetype master) → `parse_document` (loader per detected type; `mark_parse_failed` after the last retry or the 10-minute timeout) → `ocr_llm` (if needed)                                                                                                                                                                                                                                                                                                                           | —                                                                |
| `SubSkillBuild`                            | `build_manifest` → `check_libraries` → `render_skeleton` → `generate_bodies` → `assemble` → `ast_gate` → `static_gate` → `generate_tests` → `build_promptfoo_suite` → `run_sandbox_tests` (loop harness) → `certify_sub_skill`; on failure `correct` (≤2) then loop; then **wait `retry`**                                                                                                                                 | `retry`, `cancel`                                             |
| `PurgeDeleted` (daily schedule)            | purge documents past`purge_after`                                                                                                                                                                                                                                                                                                                                                                                                                                   | —                                                                |
| `ExportPackage`                            | assemble zip (manifests, code, schema.sql, evidence, checksums)                                                                                                                                                                                                                                                                                                                                                                                                       | —                                                                |

Every stage calls `notify` (N = Next/Notification): an in-app notification plus a state update through the state machine service.

## 13.3 Retries and timeouts

| Activity                                                                              | Retry                                      | Start-to-close          | Non-retryable                   |
| ------------------------------------------------------------------------------------- | ------------------------------------------ | ----------------------- | ------------------------------- |
| `scan_clamav`                                                                       | 5, backoff 5 s×2                          | 2 min                   | `Infected`                    |
| `parse`                                                                             | 3, 10 s×2                                 | 10 min, heartbeat 60 s  | `Unsupported`, `Corrupt`    |
| `ocr_llm`                                                                           | 3                                          | 10 min                  | `Unreadable`                  |
| `build_knowledge` (Cognee)                                                          | 5, 30 s×2                                 | 60 min, heartbeat 2 min | `EmptyCorpus`                 |
| `build_okf_bundle`                                                                  | 3                                          | 15 min                  | `BundleInvalid`               |
| `generate_sow`, `extract_*`, `generate_bodies`, `generate_tests`, `correct` | 3 (transport) + internal schema re-ask ≤3 | 15 min                  | `SchemaInvalidAfterRetries`   |
| `build_schema_sql`                                                                  | 2                                          | 5 min                   | `SchemaInvalid` (→ FDE)      |
| `render_skeleton`, `assemble`                                                     | 2                                          | 2 min                   | `TemplateError`               |
| `ast_gate`, `static_gate`                                                         | 2                                          | 10 min                  | `GateFailed` (→ correction)  |
| `run_sandbox_tests`                                                                 | 3 for infrastructure only                  | 20 min, heartbeat 30 s  | `TestsFailed` (→ correction) |
| `certify_*`, `freeze_and_version`                                                 | 5                                          | 2 min                   | `HashMismatch`                |

Human waits have no timeout; a reminder notification is sent after 3 days *(proposed)*.

## 13.4 Idempotency and recovery

- Activities upsert by natural keys (`sub_skill_id + build_no + attempt + gate`). Storage writes are content-addressed. LLM results are cached by `(temporal_workflow_id, activity_id, attempt)`.
- Freeze is ordered: files → read-only → DB transaction (certification + version + `is_frozen` + lifecycle `APPROVED`) → read-back hash verification. A failure leaves the version not frozen, and a re-run completes it.
- Replay tests of recorded histories run in CI (`replay.yml`, required check `temporal-replay`): every `server/tests/temporal/histories/*.json` replays against the PR's workflow code, and the histories cover correction exhaustion, cancel and `TESTING_BLOCKED`. Workflow-code changes use `workflow.patched`. `make record-histories` re-records them; keep the old file as `<name>_before_<change>.json` while older runs can still be live.

---
[← §12](12-code-generation.md)   [Design index](README.md)   [§14 →](14-state-machines.md)
