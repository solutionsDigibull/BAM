> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §8. API design


## 8.1 Conventions

- Base `/api/v1`. Admin APIs under `/admin/...` (role `sbam-admin`). FDE APIs under `/projects/{project_id}/workflows/{workflow_id}/...` (role `sbam-fde` + project assignment).
- Auth: `Authorization: Bearer <Keycloak access token>`. Tenant comes from the token claim and must match the resource; a mismatch returns **404**.
- Errors: `application/problem+json` with `type`, `title`, `status`, `detail`, `code`, `errors[]` (`path`, `message`), `correlation_id`.
- Concurrency: `ETag` = `row_version`; writes require `If-Match` (412 if mismatched, 428 if missing). Exception: SOW saves send `base_version`; a stale base returns **409** with a diff ([TD [§8.7](08-api-design.md)](08-api-design.md)).
- Commands that start long work return **202** + a `run` object; clients poll `GET …/runs/{id}` every 3–5 s (SSE optional later).
- Idempotency: `Idempotency-Key` header on Generate/Retry/Export.
- Pagination: `?cursor=&limit=`; filtering `?status=`; sorting `?sort=-created_at`.
- Status codes: 200, 201, 202, 204, 400, 401, 403 (wrong portal/role), 404 (missing or other tenant/project), 409 (state conflict), 412, 413, 415, 422, 423 (locked/frozen), 429, 503.

Paths below are relative to `/api/v1`. **P** = `/projects/{project_id}` (project-level: uploads, documents, knowledge, SOW, pipeline). **W** = `/projects/{project_id}/workflows/{workflow_id}` (the Workflow SBAM generated: Macro/Sub-Skills, builds, manifests, certification, versions). There is no API for an FDE to create a Workflow.

## 8.2 Identity, tenants and users

| Method   | Endpoint                   | Request                                     | Response                               | Role        | Errors         |
| -------- | -------------------------- | ------------------------------------------- | -------------------------------------- | ----------- | -------------- |
| GET      | `/me`                    | —                                          | user, tenant, roles, assigned projects | any         | 401            |
| GET/POST | `/admin/tenants`         | `name`                                    | tenants                                | super-admin | 409            |
| GET      | `/admin/users`           | filters                                     | users of tenant                        | admin       | —             |
| POST     | `/admin/users/invite`    | `email, roles[]`                          | user (Keycloak invite)                 | admin       | 409, 422       |
| PATCH    | `/admin/users/{user_id}` | `roles[], active`                         | user                                   | admin       | 409 last admin |
| GET      | `/admin/audit`           | `entity_type, entity_id, actor, from, to` | entries                                | admin       | 422            |

## 8.3 Projects and masters (Admin)

| Method    | Endpoint                                      | Request                                                                                                                                                                                                         | Response                                                                                               | Role              | Validation / errors                                                                                                                                                                           |
| --------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET/POST  | `/admin/projects`                           | `name, client_name, description, context_notes`                                                                                                                                                               | project                                                                                                | admin             | unique name per tenant (409)                                                                                                                                                                  |
| GET/PATCH | `/admin/projects/{project_id}`              | partial +`If-Match`                                                                                                                                                                                           | project                                                                                                | admin             | 412                                                                                                                                                                                           |
| POST      | `/admin/projects/{project_id}/archive`      | —                                                                                                                                                                                                              | project                                                                                                | admin             | 409 if Workflow building                                                                                                                                                                      |
| GET/PUT   | `/admin/projects/{project_id}/members`      | `user_ids[]`                                                                                                                                                                                                  | members                                                                                                | admin             | users must be FDE in tenant                                                                                                                                                                   |
| GET       | `/admin/master-types`                       | —                                                                                                                                                                                                              | types + JSON Schemas                                                                                   | admin             | —                                                                                                                                                                                            |
| GET       | `/admin/masters/{type}`                     | `scope=tenant or project_id`                                                                                                                                                                                  | entries + active version                                                                               | admin             | 404                                                                                                                                                                                           |
| POST      | `/admin/masters/{type}`                     | `key, project_id?, payload`                                                                                                                                                                                   | entry v1                                                                                               | admin             | 422 schema                                                                                                                                                                                    |
| POST      | `/admin/masters/{type}/{entry_id}/versions` | `payload`                                                                                                                                                                                                     | new version                                                                                            | admin             | 422                                                                                                                                                                                           |
| POST      | `/admin/masters/import`                     | consolidated masters workbook`.xlsx` (MSC / BAM 0.2 layout: tabs README, Organization, Rules, Agents, Skills, Filetype; 3 header rows; data from row 4) + `mode` (validate-only / upsert) + `project_id?` | import report per tab and row (created, updated, unchanged, errors; stable IDs and statuses extracted) | admin             | 422 if tab names, column names/order or header rows differ from the frozen structure; per-row errors (e.g., invalid RAG Reference JSON); Filetype tab differences → 403 `FILETYPE_FROZEN` (super-admin: `/admin/filetypes/import`); infected → 422 `MALWARE`, quarantined (same code as uploads) |
| GET       | `/admin/masters/export`                     | `project_id?`                                                                                                                                                                                                 | `.xlsx` in the same template layout                                                                  | admin             | —                                                                                                                                                                                            |
| GET       | `/admin/masters/template`                   | —                                                                                                                                                                                                              | empty BAM 0.2 template`.xlsx` (headers + README)                                                     | admin             | —                                                                                                                                                                                            |
| GET       | `/admin/filetypes`                          | —                                                                                                                                                                                                              | 43 frozen Filetype entries + MVP loader flag + default route                                           | admin, FDE (read) | —                                                                                                                                                                                            |
| POST      | `/admin/filetypes/import` (E3.13, ADR 0020) | multipart: `file` (.xlsx) + `mode` (`validate-only`\|`upsert`) + `version_label?` | ImportReport; one platform version per changed Filetype row, audited `FILETYPE_VERSION_IMPORTED` | super-admin | 403, 413, 415, 422 (`STRUCTURE_MISMATCH`), 409 |
| POST      | `/admin/master-documents`                   | file +`project_id?` + `master_type?` (override)                                                                                                                                                             | master document (routed by Filetype default route unless overridden)                                   | admin             | 413, 415, infected → 422                                                                                                                                                                     |
| GET       | `/admin/master-documents`                   | `master_type`, `project_id`                                                                                                                                                                                 | list with route, scan and parse status                                                                 | admin             | —                                                                                                                                                                                            |
| PATCH     | `/admin/master-documents/{doc_id}`          | `master_type`, `master_entry_id?`                                                                                                                                                                           | re-routed document                                                                                     | admin             | 422                                                                                                                                                                                           |
| GET       | `/admin/registry`                           | —                                                                                                                                                                                                              | runtime registry (digest, packages)                                                                    | admin (FDE read)  | —                                                                                                                                                                                            |

## 8.4 Workflows (FDE)

| Method   | Endpoint                             | Request                            | Response                                          | Errors           |
| -------- | ------------------------------------ | ---------------------------------- | ------------------------------------------------- | ---------------- |
| GET      | `/projects`                        | —                                 | assigned projects                                 | —               |
| GET | `P/workflows` | — | Workflows SBAM generated for this project (one per approved SOW version) | 404 not assigned (no existence leak) |
| POST | `P/pipeline/start` | — | 202; starts `ProjectPipeline` (Start processing) | 409 already running |
| GET | `P/pipeline` | — | project pipeline state + step timeline + counts | 404 |
| GET      | `W`                                | —                                 | workflow + state + step timeline + counts         | 404              |
| PATCH | `W` | `name` + `If-Match` | rename only (Workflow is created by SBAM) | 423 if frozen |
| GET      | `W/runs/{run_id}`                  | —                                 | Temporal run mirror (stage, progress, last error) | 404              |
| POST     | `W/cancel`                         | `reason`                         | 202                                               | 409 if frozen    |

## 8.5 Documents

| Method | Endpoint                               | Request                         | Response                                  | Errors                             |
| ------ | -------------------------------------- | ------------------------------- | ----------------------------------------- | ---------------------------------- |
| POST   | `P/uploads`                          | `files[]: name, size, mime`   | tus endpoint + short-lived upload token   | 413 (>15 MB), 409 (>20 files), 415 |
| POST   | `/internal/tusd/hooks`               | tusd hook                       | accept/reject                             | 401 (hook secret)                  |
| GET    | `P/documents`                        | `include_deleted`             | documents + status per stage              | —                                 |
| POST   | `P/documents/process`                | —                              | 202 (start/continue ingestion)            | 409 none uploaded                  |
| POST   | `P/documents/{document_id}/retry`    | —                              | 202                                       | 409 not failed                     |
| DELETE | `P/documents/{document_id}`          | —                              | 204 (soft delete;`purge_after` = +90 d) | 423 if used by frozen version      |
| POST   | `P/documents/{document_id}/restore`  | —                              | document                                  | 410 after purge                    |
| GET    | `P/documents/{document_id}/download` | `variant=original or derived` | 302 short-lived URL (signed local URL)    | 404                                |

## 8.6 Knowledge

| Method | Endpoint                                | Request                    | Response                          | Errors       |
| ------ | --------------------------------------- | -------------------------- | --------------------------------- | ------------ |
| GET    | `P/knowledge`                         | —                         | versions, status, coverage        | —           |
| POST   | `P/knowledge/rebuild`                 | —                         | 202 (new version)                 | 409 building |
| GET    | `P/knowledge/{kv_id}/graph`           | `cursor, limit, type, q` | nodes + edges (paged)             | 404          |
| GET    | `P/knowledge/{kv_id}/nodes/{node_id}` | —                         | node, neighbours, source passages | 404          |
| GET    | `P/knowledge/{kv_id}/okf-bundle`      | —                         | 302 download                      | 404          |

## 8.7 SOW

| Method | Endpoint                            | Request                                         | Response                                           | Errors                  |
| ------ | ----------------------------------- | ----------------------------------------------- | -------------------------------------------------- | ----------------------- |
| GET    | `P/sow`                           | —                                              | SOW + current + approved version                   | 404                     |
| GET    | `P/sow/versions`                  | —                                              | list (version, source, sha256, author, time)       | —                      |
| GET    | `P/sow/versions/{v}`              | —                                              | TipTap JSON + structured + citations               | 404                     |
| POST   | `P/sow/versions`                  | `content_tiptap, base_version` + `If-Match` | new version                                        | 409, 422, 423 approved  |
| POST   | `P/sow/regenerate`                | `hint?`                                       | 202                                                | 409 approved            |
| GET    | `P/sow/versions/{v}/diff/{other}` | —                                              | section diff                                       | —                      |
| POST   | `P/sow/versions/{v}/approve`      | `sha256`                                      | approved version; pipeline signalled               | 409 stale or not latest; 503 if Temporal is unavailable (approval rolled back — approval and `sow_approved` signal succeed or fail together) |
| POST   | `P/sow/new-version`               | `reason`                                      | draft version from the approved one (scope change) | 409 none approved       |
| GET    | `P/sow/versions/{v}/impact`       | —                                              | changed sections, affected Macro/Sub-Skills        | —                      |
| GET    | `P/sow/versions/{v}/export`       | `format=pdf or docx or md`                    | 302 download                                       | 422                     |

## 8.8 ISATVON

| Method | Endpoint                                 | Request                       | Response                            | Errors                                                    |
| ------ | ---------------------------------------- | ----------------------------- | ----------------------------------- | --------------------------------------------------------- |
| GET    | `/schemas/isatvon/{version}`           | —                            | JSON Schema + grid metadata schema  | 404                                                       |
| GET    | `W/macro-skills`                       | —                            | Macro Skills with Sub-Skills (tree) | —                                                        |
| PATCH  | `W/sub-skills/{sub_id}`                | partial ISATVON +`If-Match` | Sub-Skill +`validation_errors[]`  | 422, 412, 423                                             |
| DELETE | `W/sub-skills/{sub_id}`                | —                            | 204                                 | 423 if part of a frozen version (new version required) |
| POST   | `W/macro-skills/{macro_id}/regenerate` | `hint?`                     | 202                                 | 409 building                                              |
| POST   | `W/sub-skills/{sub_id}/regenerate`     | `hint?`                     | 202                                 | 409                                                       |
| PATCH  | `W/macro-skills/{macro_id}`            | `name, description, order`  | macro                               | 422                                                       |
| POST   | `W/isatvon/validate`                   | —                            | validation report                   | —                                                        |
| GET    | `W/isatvon/export.xlsx`                | —                            | 302 xlsx                            | —                                                        |

`GET /schemas/isatvon/{version}` (E8.6) is readable by any signed-in user and holds no tenant data. Only `1.4` is served; any other version is 404 problem+json. The body is `{version, json_schema, grid}`: `json_schema` is the `SubSkill` JSON Schema and `grid.column_groups` lists the groups Metadata, I, S, A, T, V, O, N, Status in that order. Each child column has `field_path` (dotted, e.g. `isatvon_definition.v_variables.max_iterations`), `header`, `type`, `editor` (one of `text`, `largeText`, `number`, `boolean`, `json`, `expression`, `stringList`, `select`, `readonly`), `required`, `editable`, `platform` and optionally `enum`, `min`, `max`, `width`. Columns are generated from the model, so a new field gets the default editor for its type. Platform fields (`skill_id`, `state`, `content_sha256`, `validation_errors`) are `editable: false`; the last three live on the Sub-Skill row, so their paths are not in `json_schema`. The response carries a strong `ETag` and `Cache-Control: private, max-age=300, must-revalidate`; a matching `If-None-Match` answers 304 with no body.

## 8.9 Manifests

| Method | Endpoint                           | Request                                 | Response                                                         | Errors                        |
| ------ | ---------------------------------- | --------------------------------------- | ---------------------------------------------------------------- | ----------------------------- |
| GET    | `/schemas/manifest/{version}`    | —                                      | Sub-Skill + Workflow manifest schemas                            | 404                           |
| GET    | `W/sub-skills/{sub_id}/manifest` | `version?`                            | manifest + sha256                                                | 404                           |
| GET    | `W/manifest`                     | `version?`                            | Workflow manifest                                                | 404                           |
| GET    | `W/schema-sql`                   | `version?`                            | Workflow foundational`schema.sql` + SCHEMA_SQL status, findings + tables (`WorkflowSchemaSql`, E9.4) | 404                           |
| PUT    | `W/schema-sql/source`            | `document_id` of an uploaded `.sql` | marks it as the base schema (O-16); a new PENDING version the next build applies | 422 `SCHEMA_PARSE_ERROR` (statement index; also a statement outside the DDL allowlist), 422 not a parsed clean `.sql`, 409 `WORKFLOW_BUILDING`, 423 `WORKFLOW_FROZEN` |

## 8.10 Build orchestration and gates

| Method | Endpoint                        | Request                                                      | Response                                    | Errors                                    |
| ------ | ------------------------------- | ------------------------------------------------------------ | ------------------------------------------- | ----------------------------------------- |
| POST   | `W/generate`                  | `sub_skill_ids?` (default all valid) + `Idempotency-Key` | 202 + build runs                            | 409 SOW not approved / invalid Sub-Skills |
| GET    | `W/builds`                    | —                                                           | per Sub-Skill: stage, attempt, gate summary | —                                        |
| GET    | `W/sub-skills/{sub_id}/gates` | `attempt?`                                                 | gate results with findings                  | 404                                       |
| POST   | `W/sub-skills/{sub_id}/retry` | `reason`                                                   | 202 (after FDE fix)                         | 409 not in`NEEDS_FDE_REVIEW`/failed     |

## 8.11 Code

| Method | Endpoint                                             | Request   | Response                                    | Errors |
| ------ | ---------------------------------------------------- | --------- | ------------------------------------------- | ------ |
| GET    | `W/sub-skills/{sub_id}/code/attempts`              | —        | attempts (template version, reason, status) | —     |
| GET    | `W/sub-skills/{sub_id}/code/attempts/{n}/files`    | `path?` | file tree / content (read-only)             | 404    |
| GET    | `W/sub-skills/{sub_id}/code/attempts/{n}/diff/{m}` | —        | unified diff                                | 404    |

## 8.12 Test data, Promptfoo and sandbox

| Method | Endpoint                                        | Request                         | Response                                | Errors                           |
| ------ | ----------------------------------------------- | ------------------------------- | --------------------------------------- | -------------------------------- |
| GET    | `W/sub-skills/{sub_id}/tests`                 | —                              | dataset + cases + last results          | —                               |
| PUT    | `W/sub-skills/{sub_id}/tests/cases/{case_id}` | `input, expected, assertions` | case (`edited_by_fde`)                | 422                              |
| POST   | `W/sub-skills/{sub_id}/tests/cases`           | case                            | case (kind FDE)                         | 422                              |
| DELETE | `W/sub-skills/{sub_id}/tests/cases/{case_id}` | —                              | 204                                     | 409 if fewer than 3 would remain |
| GET    | `W/sub-skills/{sub_id}/sandbox-runs`          | —                              | runs (mode, duration, failure category) | —                               |
| GET    | `W/sandbox-runs/{run_id}/logs`                | —                              | 302 logs                                | 404                              |

## 8.13 Notifications

| Method | Endpoint                                  | Request         | Response |
| ------ | ----------------------------------------- | --------------- | -------- |
| GET    | `/notifications`                        | `unread_only` | list     |
| POST   | `/notifications/{notification_id}/read` | —              | 204      |
| POST   | `/notifications/read-all`               | —              | 204      |

## 8.14 Certification, versions and export

| Method | Endpoint                              | Request             | Response                                                                             | Errors         |
| ------ | ------------------------------------- | ------------------- | ------------------------------------------------------------------------------------ | -------------- |
| GET    | `W/certification`                   | —                  | tree Workflow → Macro → Sub-Skill with states, evidence ids, hashes, reuse markers | —             |
| GET    | `W/versions`                        | —                  | Workflow versions (MAJOR.0.PATCH, SOW version, frozen_at, sha256)                    | —             |
| GET    | `W/versions/{version}`              | —                  | version detail + lineage                                                             | 404            |
| GET    | `W/versions/{version}/diff/{other}` | —                  | changed Sub-Skills                                                                   | 404            |
| POST   | `W/versions/{version}/export`       | `Idempotency-Key` | 202 → package                                                                       | 409 not frozen |
| GET    | `W/versions/{version}/export`       | —                  | 302 zip + checksums                                                                  | 404            |
| POST   | `W/sub-skills/{sub_id}/new-version` | `reason`          | Sub-Skill draft (patch version flow)                                                 | 409 not frozen |

There are no certify endpoints: certification is automatic (C-02). There are no revoke endpoints (C-31). The SRIM handover API is future scope (`/handover/*` reserved).

## 8.15 Registry

| Method | Endpoint      | Response                                                            |
| ------ | ------------- | ------------------------------------------------------------------- |
| GET    | `/registry` | current runtime image digest + allowed packages (read-only for FDE) |

## 8.16 Audit and activity

| Method | Endpoint         | Response                            | Role  |
| ------ | ---------------- | ----------------------------------- | ----- |
| GET    | `W/activity`   | Workflow-scoped audit/activity feed | FDE   |
| GET    | `/admin/audit` | tenant audit (see 8.2)              | Admin |

---
[← §7](07-database-design.md)   [Design index](README.md)   [§9 →](09-frontend-design.md)
