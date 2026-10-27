> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# TD §7. Database design


## 7.1 Conventions

- PostgreSQL 16; schema `sbam`; UUIDv7 keys; `timestamptz` UTC; `created_at/by`, `updated_at`, `row_version` on mutable tables; `deleted_at` for soft delete.
- **Every table has `tenant_id`**, and every project-owned table also has `project_id`. The repository base class always filters by them.
- Status columns are text + `CHECK` constraints generated from the Python enums.
- JSONB for schema-validated documents (ISATVON, manifests, SOW structure) with a `schema_version` column.
- Files never go in the database: only a storage key + SHA-256 + size + content type.
- Frozen rows are protected by `BEFORE UPDATE OR DELETE` triggers (`is_frozen = true` → raise).
- Audit: a normal append-only table (C-42).

### 7.1.1 Implementation (E1.5, ADR 0003)

Code lives in `server/api/sbam_api/db/`. It uses sync SQLAlchemy 2 on psycopg 3. `DATABASE_URL` stays a plain `postgresql://` URL, and `make_engine` adds the driver.

- **Tables.** Declare a table as `class X(ProjectOwned, Base)` (or `TenantOwned`). `Base.metadata` has `schema="sbam"` and a constraint naming convention (`pk_`, `fk_`, `ix_`, `uq_`, `ck_<table>_<name>`).
  - Ids come from `core/ids.uuid7()`: RFC 9562 UUIDv7, with a 12-bit counter, so ids made by one process stay ordered within a millisecond and across clock steps.
  - `row_version` is SQLAlchemy's `version_id_col`, so every UPDATE carries `WHERE row_version = <loaded>`.
- **Status columns.** `status_check(Enum)` or `status_check(check_constraint_values("<machine>"))` builds the `CHECK (status IN (...))`.
- **Repositories.** Feature code subclasses `TenantScopedRepository[X]` (`get` / `list` / `add` / `update(entity, expected_row_version)` / `soft_delete`) and builds it with a `TenantContext` from auth (#48).
  - No context raises `TenantContextMissing` before any query runs.
  - Other-tenant and soft-deleted rows read as `None`, which the API answers with 404.
  - A stale version raises `StaleRowVersion`, which renders as 412.
  - A frozen row raises `RowFrozen`, which renders as 423.
  - Both errors are `ApiError`s.
- **Frozen rows.** Opt a table in from its migration with `op.execute(create_frozen_trigger("<table>"))`. The shared function `sbam.forbid_frozen_change()` comes from the baseline and raises SQLSTATE `SB423`.
- **Lint.** Ruff `TID251` bans `sqlalchemy.select/insert/update/delete/text/create_engine` outside `sbam_api/db/`, `migrations/`, `*repository.py` and tests.
- **Migrations.**
  - They live in `sbam_api/migrations/` (packaged, no `alembic.ini`), and the version table is in `sbam`.
  - Run them with `uv run python -m sbam_api.db.migrate <alembic args>`. No args means `upgrade head`, which is what the `api-migrate` job runs.
  - `make migrations-check` (part of `make check`) fails on more than one head.
  - Downgrades undo only their own revision. The baseline never drops the schema.

### 7.1.2 File storage (E1.6, C-37)

Code lives in `server/api/sbam_api/storage/`. Project files go only through a `StorageBackend` (`put` / `open` / `get(verify=)` / `exists`). The database keeps the returned `StoredObject` fields (`storage_key`, `sha256`, `size`, `content_type`) plus the user's filename.

- **Keys.** A key looks like `<tenant>/<project>/[<workflow>/]<kind>/<sha256>`, where kind is one of `uploads`, `derived`, `okf`, `code`, `tests`, `exports`.
  - Keys are content-addressed and never built from a user filename.
  - The same bytes in the same scope get the same key (dedupe).
  - Every key is validated by a regex before any IO, so `..`, absolute paths and backslashes are rejected with 400.
- **Local disk** (`STORAGE_BACKEND=local`, the default).
  - Writes stream into `STORAGE_ROOT/.tmp/`, hash and fsync there, then `os.replace` into place, so readers never see a partial object.
  - Objects are mode `0444` and never overwritten.
  - An IO error such as disk full returns 503 `SERVICE_UNAVAILABLE` and leaves no partial file.
  - Tmp files older than 1 h are removed on start.
  - On Windows hosts, keep `STORAGE_ROOT` short: keys are about 180 characters and MAX_PATH is 260.
- **S3 stub** (`STORAGE_BACKEND=s3`, `S3_BUCKET`, optional `S3_ENDPOINT_URL`). It uses the same keys and interface and passes the same test suite on moto.
- **Signed downloads.**
  - `signed_url(key, TenantContext, expires_in)` signs with HMAC-SHA256 over key + expiry + tenant, valid for 1–900 s. A key from another tenant gives 404.
  - The URL is served by `GET /api/v1/files/{key}?exp=&sig=`. A tampered or expired link gets 403.
  - `STORAGE_SIGNING_KEY` must be at least 32 characters. While it is missing or a placeholder, signed downloads answer 503.

## 7.2 Tenancy, identity, projects

| Table               | Purpose                        | Key fields                                                                               | Relations      | Status / notes            |
| ------------------- | ------------------------------ | ---------------------------------------------------------------------------------------- | -------------- | ------------------------- |
| `tenants`         | Client organisation            | `id`, `name`, `keycloak_org_id`, `status`                                        | 1→N projects  | `ACTIVE`, `SUSPENDED` |
| `users`           | Local mirror of Keycloak users | `id`, `keycloak_sub`, `tenant_id`, `email`, `display_name`, `portal_roles[]` | ← members     | Synced on login           |
| `projects`        | Admin-created project          | `tenant_id`, `name`, `client_name`, `description`, `context_notes`, `status` | 1→N workflows | `ACTIVE`, `ARCHIVED`  |
| `project_members` | FDE assignment                 | `project_id`, `user_id`, `role` (`FDE`)                                          | —             | —                        |

## 7.3 Masters and registry

| Table                     | Purpose                                         | Key fields                                                                                                                                                                                                                                                                              | Notes                                                                                              |
| ------------------------- | ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `master_types`          | The five masters + README with JSON Schema      | `code` (ORGANIZATION, RULES, AGENTS, SKILLS, FILETYPE, README), `schema` (JSONB), `structure_version` (`BAM 0.2`)                                                                                                                                                               | Schemas in [§7.3.1](07-database-design.md) (frozen BAM 0.2 columns)                                                        |
| `master_entries`        | Entry identity                                  | `tenant_id`, `project_id` (null = tenant-scoped), `master_type`, `row_label` (`Unit #n`, `Rule #n`, `Agent #n`, `Skill #n`, `Filetype #n`), `stable_id` (e.g. `ORG-EMS`, `RULE-BOM`, `AG-INTAKE`, `SK-BOM`), `status` (parsed from Misc), `source_kind` | Tenant-scoped; a project binding narrows context; Filetype entries are platform-wide and read-only |
| `master_entry_versions` | Versioned payload (exact BAM 0.2 column values) | `entry_id`, `version`, `payload` (JSONB), `workbook_version`                                                                                                                                                                                                                    | Immutable versions                                                                                 |
| `filetype_row_versions` | Platform versions of the 43 Filetype rows (no tenant; E3.13, ADR 0020) | `row_label`, `version`, `payload` (JSONB), `workbook_sha256`, `version_label`, `created_by` | Append-only; a row with no version is the shipped row |
| `master_snapshots`      | Exact masters used by a knowledge build         | `project_id`, `entries[]` (id + version), `sha256`                                                                                                                                                                                                                                | Referenced by`knowledge_versions`                                                                |
| `runtime_registry`      | Libraries available in`skill-runtime`         | `image_digest`, `package`, `version`, `import_names[]`, `allowed`                                                                                                                                                                                                             | Generated by the image build (C-25)                                                                |
| `code_templates`        | Skeleton template versions                      | `name`, `version`, `sha256`, `params_schema`                                                                                                                                                                                                                                    | Managed in Git (O-06)                                                                              |

### 7.3.1 Master schemas (from *BuLLM – Admin Masters (One Sheet) – Consolidated.xlsx*, BAM 0.2 structure)

Source: **MSC** = *BuLLM – Admin Masters (BAM) – Consolidated*, sheet version 0.1. Its structure was **accepted and frozen by the owner on 5 Oct 2026**: tab names, column names and order, row labels and the 43 filetypes. Content is still Proposed. MSC supersedes the earlier Manufacturing Template, whose rows are now carried inside the BAM 0.2 columns.

**Workbook layout (import/export must match exactly):**

- Tabs: `README`, `Organization`, `Rules`, `Agents`, `Skills`, `Filetype` (exactly 5 masters + README).
- Each master tab keeps three header rows: row 1 **Metadata** (column numbering), row 2 **Name** (column name), row 3 **Description**. Data starts at row 4.
- Column A holds the **row label**: `Unit #n` (Organization), `Rule #n`, `Agent #n`, `Skill #n`, `Filetype #n`.
- The **stable ID** (e.g., `ORG-EMS`, `RULE-BOM`, `AG-INTAKE`, `SK-INTAKE`) is written *inside* the row, not in its own column (see "Stable ID extraction" below).
- There is **no Status column**. Status is written inside the Misc cell (e.g., `Status: Proposed`). No content is Approved yet.
- No columns may be added, removed, renamed or reordered. Any structure change needs a new workbook version and owner sign-off.

| Master                 | Col | Column name (row 2)  | Description (row 3)                                                                        | SBAM field                                              | Type                                        |
| ---------------------- | --- | -------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------- | ------------------------------------------- |
| **Organization** | A   | Name                 | Row label`Unit #n`                                                                       | `row_label`                                           | string                                      |
|                        | 1   | Org Name             | Company Name                                                                               | `org_name`                                            | string                                      |
|                        | 2   | Type                 | Headquarters / Branch / Department                                                         | `type`                                                | enum HQ, BRANCH, DEPARTMENT (blank allowed) |
|                        | 3   | Code                 | Code for the HQ/Branch/Department                                                          | `code` (stable ID, e.g., `BU-AERO-01`, `ORG-EMS`) | string                                      |
|                        | 4   | Location             | Address                                                                                    | `location`                                            | string                                      |
|                        | 5   | Org Details          | What the company/branch does                                                               | `org_details`                                         | text                                        |
|                        | 6   | Employees            | User Role                                                                                  | `employees`                                           | text                                        |
|                        | 7   | Customers            | Client Accounts                                                                            | `customers`                                           | text                                        |
|                        | 8   | Offerings            | Product/Assembly Lines                                                                     | `offerings`                                           | text                                        |
|                        | 9   | Competencies         | Employee Skill Mapping                                                                     | `competencies`                                        | text                                        |
|                        | 10  | Data Sources         | Knowledge Bases & Vector Stores                                                            | `data_sources` (pointers only)                        | text                                        |
|                        | 11  | Systems/Tools        | ERP & API Integration Endpoints                                                            | `systems_tools`                                       | text                                        |
|                        | 12  | Assets               | HW, SW & Client Devices                                                                    | `assets`                                              | text                                        |
|                        | 13  | Certifications       | Quality & Regulatory Standards                                                             | `certifications`                                      | text                                        |
|                        | 14  | Misc / Audit         | Dynamic Overrides & Audit Logs (status inside)                                             | `misc_audit`                                          | text                                        |
| **Rules**        | A   | Name                 | Row label`Rule #n`                                                                       | `row_label`                                           | string                                      |
|                        | 0   | Domain Rule          | Live Costing & Margin Price Break Sheet (template rows:`RULE-x: applies to. Policy: …`) | `domain_rule`                                         | text                                        |
|                        | 1   | PCB Panelization     | Base PCB Dimensions & Yields                                                               | `pcb_panelization`                                    | text                                        |
|                        | 2   | Components (BOM)     | SMT / PTH Part Line Items                                                                  | `components_bom`                                      | text                                        |
|                        | 3   | Materials & Finish   | Laminates & Surface Coatings                                                               | `materials_finish`                                    | text                                        |
|                        | 4   | Box Build            | Enclosures & Mechanical Assembly                                                           | `box_build`                                           | text                                        |
|                        | 5   | Machine HR Rates     | Machine Center Rate Database                                                               | `machine_hr_rates`                                    | text                                        |
|                        | 6   | Outsourced Services  | Third-Party Subcontract Costs                                                              | `outsourced_services`                                 | text                                        |
|                        | 7   | Assembly Routing     | Calculated Assembly Labor Steps                                                            | `assembly_routing`                                    | text                                        |
|                        | 8   | Rules (DFM/Sourcing) | Automated Margin & Constraint Checks (template: validation / stop condition)               | `rules_dfm_sourcing`                                  | text                                        |
|                        | 9   | Misc                 | Cost Adjustments & Overrides (status inside)                                               | `misc`                                                | text                                        |
|                        | 10  | RAG Reference        | JSON object (keys below)                                                                   | `rag_reference`                                       | JSON                                        |
| **Agents**       | A   | Name                 | Row label`Agent #n`                                                                      | `row_label`                                           | string                                      |
|                        | 1   | Agent Name           | Name of the Agent                                                                          | `agent_name`                                          | string                                      |
|                        | 2   | Description          | What the agent does (template rows:`AG-x. Allowed input … Required output …`)          | `description`                                         | text                                        |
|                        | 3   | Model                | LLMs (Local/Public)                                                                        | `model`                                               | string                                      |
|                        | 4   | Repo                 | Tool Repositories                                                                          | `repo`                                                | string                                      |
|                        | 5   | API                  | OpenAPI Schemas                                                                            | `api`                                                 | string                                      |
|                        | 6   | MCP Server           | FastMCP Proxy Gateway                                                                      | `mcp_server`                                          | string                                      |
|                        | 7   | Script               | Pre-Compiled Executable Python/JS Scripts                                                  | `script`                                              | string                                      |
|                        | 8   | Prompt               | Prompt to generate output                                                                  | `prompt`                                              | string                                      |
|                        | 9   | Eval                 | Benchmark                                                                                  | `eval`                                                | string                                      |
|                        | 10  | Logs                 | Test Logs                                                                                  | `logs`                                                | string                                      |
|                        | 11  | Misc                 | Any other info to be added by FDE (review gate, status inside)                             | `misc`                                                | text                                        |
| **Skills**       | A   | Name                 | Row label`Skill #n`                                                                      | `row_label`                                           | string                                      |
|                        | 0   | Skill                | Skills required for the project (template rows:`SK-x: workflow step`)                    | `skill`                                               | string                                      |
|                        | 1   | Documents            | Ingest PDFs/SOWs (source input reference)                                                  | `documents`                                           | text                                        |
|                        | 2   | RFQ / Inquiries      | Client RFQ Header Data (critical keys extracted)                                           | `rfq_inquiries`                                       | text                                        |
|                        | 3   | Currency             | FX Rates for Quotation                                                                     | `currency`                                            | text                                        |
|                        | 4   | Supplier Master      | Distributor Matrix                                                                         | `supplier_master`                                     | text                                        |
|                        | 5   | Commodity Pricing    | Raw Spot Price Feeds                                                                       | `commodity_pricing`                                   | text                                        |
|                        | 6   | Shipping & Logistics | IncoTerms & Freight Rates                                                                  | `shipping_logistics`                                  | text                                        |
|                        | 7   | Templates            | AG Grid Layout Templates (output contract)                                                 | `templates`                                           | text                                        |
|                        | 8   | HITL Governance      | Circuit Breaker Trigger Rules (validation / human review)                                  | `hitl_governance`                                     | text                                        |
|                        | 9   | Misc                 | Custom Project Context (status inside)                                                     | `misc`                                                | text                                        |
|                        | 10  | RAG Reference        | JSON object (keys below)                                                                   | `rag_reference`                                       | JSON                                        |
| **Filetype**     | A   | Name                 | `Filetype #1` … `Filetype #43`                                                        | `row_label`                                           | string                                      |
|                        | 1   | File Format          | Input Filetype                                                                             | `file_format`                                         | string                                      |
|                        | 2   | Tier                 | RAG Tier (1, 2, 3)                                                                         | `tier`                                                | int                                         |
|                        | 3   | Purpose              | How used?                                                                                  | `purpose`                                             | text                                        |
|                        | 4   | RAG Ingestion        | How the RAG ingests the input documents                                                    | `rag_ingestion`                                       | text                                        |
|                        | 5   | Example              | Link to Sample File                                                                        | `example`                                             | URL                                         |

**RAG Reference JSON** (Rules col 10, Skills col 10), with keys exactly as in MSC:

```json
{ "Example": "PCBA Costing Standard", "Links / Path": "s3://domain-rules/pcba_costing_v1.json",
  "Entity / Node Type": "DomainKnowledge", "Security / Access Level": "Internal",
  "Refresh Frequency": "Quarterly", "ID Field": "DOM-PCBA-01", "Parent Entity": "ORG-001" }
```

The importer validates it as a JSON object with these 7 keys (values may be empty). `Entity / Node Type` becomes the Cognee node type, `ID Field` the node id, and `Parent Entity` an edge to an Organization node. `Security / Access Level` (e.g., Internal, Restricted) controls whether the entry is included in a project's context. `Refresh Frequency` sets the context-outdated reminder.

**SBAM system metadata** (stored in the DB alongside each entry, **never written as extra workbook columns**):

| Field                                                                   | How it is set                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stable_id`                                                           | Extracted from the row: Organization`code`; Rules `domain_rule` prefix `RULE-…:`; Agents `description` prefix `AG-…`; Skills `skill` prefix `SK-…:`; Filetype `row_label`. Falls back to `row_label` (e.g., `Agent #1`) when no stable ID is present (BAM 0.2 sample rows). |
| `status`                                                              | Parsed from`Status: <value>` in the Misc / Misc-Audit cell → PROPOSED, ILLUSTRATIVE, APPROVED, RETIRED. A missing value means PROPOSED. Changing status in the UI rewrites the `Status:` fragment in Misc, so the workbook stays round-trip identical.                                        |
| `source_kind`                                                         | `BAM_0_2_SAMPLE` (row #1 of each tab), `TEMPLATE_ROW` (carried from the Manufacturing Template), or `ADMIN_ENTRY`                                                                                                                                                                            |
| `version`, `scope` (tenant / project binding), `workbook_version` | Platform                                                                                                                                                                                                                                                                                           |

**Filetype tab rules (MSC freeze + note on the tab):**

- All 43 filetypes are **frozen** (names, order, labels, values) and the tab is a **private system table**. FDEs cannot edit it. In SBAM the Filetype master is read-only for FDEs and editable only by `super-admin` through a new workbook version (O-23): `POST /admin/filetypes/import` writes platform-wide versions to `filetype_row_versions`, and tenant imports must match them ([ADR 0020](../adr/0020-filetype-platform-versions.md)).
- SBAM's loader availability (`mvp_enabled`, [TD [§5.2](05-component-architecture.md)](05-component-architecture.md)) and the default route below are kept **outside** the tab, in platform configuration, because the tab must stay identical to its source.

**Filetype default routing (MSC README [§9](09-frontend-design.md))**, used when Admin attaches documents to masters ([§7.3.2](07-database-design.md)):

| Intent class                    | Default route (master) | File formats                                                                                 |
| ------------------------------- | ---------------------- | -------------------------------------------------------------------------------------------- |
| Agent / tool definition         | Agents                 | `.cpp`, `.go`, `.js`, `.py`, `.sql`, FastMCP Spec, OpenAPI, Swagger                |
| Rule / policy                   | Rules                  | `.json`, `.xml`, `.yaml`                                                               |
| Instruction / workflow step     | Skills                 | `.eml`, `.md`, `.msg`, `.txt`, `.docx`, `.pdf` (text-based), Google Docs         |
| Source data (RFQ / BOM / quote) | Skills                 | `.csv`, `.parquet`, `.tar.gz`, `.xlsx`, `.zip`, Google Sheets                      |
| Design artifact (CAD / PCB)     | Skills                 | ODB++,`.dwg`, `.dxf`, `.iges`, `.step`, `.stp`, Gerber RS-274X / X2 / X3, IPC-2581 |
| Knowledge source                | Organization           | Web URL / HTML                                                                               |
| Evidence / media                | Organization           | `.jpeg`, `.jpg`, `.png`, `.tiff`, `.pdf` (scanned), `.mp3`, `.mp4`, `.wav`   |

MSC marks the routes as proposals: confirm by intent class, and split mixed files by content.

**MSC guideline layer (README [§7](07-database-design.md))** is shown in the Admin portal as help text per master: which documents the FDE/Admin should refer to and the intent class. Organization is the **graph root node** for Cognee/OKF context.

**Rules carried from MSC README [§4](04-system-flow.md):** exactly 5 masters + README; BAM 0.2 structure kept exactly; capture only relevant columns, not all report data; files are not duplicated, only pointers are stored (MSC says "S3 / Postgres / DuckDB"; SBAM uses storage + PostgreSQL only, C-25); consistent row labels; keep "BAM" naming; status kept inside Misc; nothing Approved yet.

### 7.3.2 Master documents (Admin context files)

| Table                | Purpose                                                                                           | Key fields                                                                                                                                                                                                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `master_documents` | Files the Admin attaches as master context (e.g., certifications, costing standards, agent specs) | `tenant_id`, `project_id?`, `master_type` (routed), `master_entry_id?`, `filetype_row_label`, `intent_class`, `route_source` (DEFAULT_BY_FILETYPE / ADMIN_OVERRIDE / CLASSIFIER), `classifier_confidence?`, `storage_key`, `sha256`, `scan_status`, `parse_status` |

These files go through the same ClamAV → Filetype loader pipeline as FDE uploads. They are added to Cognee as master context. Per C3, FDE uploads remain the authoritative source for the project.

## 7.4 Workflow, documents, knowledge

| Table                  | Purpose                          | Key fields                                                                                                                                                                                               | Status                                                                              |
| ---------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `workflows` | Workflow generated by SBAM from an approved SOW version | `project_id`, `sow_version_id` (approved), `version` (MAJOR.0.PATCH), `state` ([§14.1](14-state-machines.md)b), `created_by` = `SYSTEM` | [§14.1](14-state-machines.md)b |
| `documents`          | Logical document                 | `project_id`, `title`, `parent_document_id` (e-mail attachment, one level), `deleted_at`, `deleted_by`, `purge_after`                                                                                                              | Soft delete (90 days)                                                               |
| `document_versions`  | Uploaded binary                  | `document_id`, `storage_key`, `sha256`, `size`, `mime`, `detected_type`, `scan_status`, `scan_signature_version`, `parse_status`, `derivative_keys`, `ocr_pages`, `parse_report` | `UPLOADED`, `SCANNING`, `REJECTED`, `PARSING`, `PARSED`, `PARSE_FAILED` |
| `ingestion_runs`     | Activity records                 | `document_version_id`, `stage`, `attempt`, `error`, `temporal_run_id`                                                                                                                          | `RUNNING`, `SUCCEEDED`, `FAILED`                                              |
| `knowledge_versions` | Cognee build + OKF Bundle        | `project_id`, `version`, `cognee_dataset`, `master_snapshot_id`, `okf_bundle_key`, `okf_sha256`, `coverage`, `status`                                                                   | `BUILDING`, `READY`, `FAILED`, `OUTDATED`                                   |
| `knowledge_sources`  | Documents in a knowledge version | `knowledge_version_id`, `document_version_id`                                                                                                                                                        | —                                                                                  |

## 7.5 SOW

| Table            | Purpose               | Key fields                                                                                                                                                                                                                                                        | Status                                                                            |
| ---------------- | --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `sows` | One per project | `project_id`, `current_version_id`, `approved_version_id` | — |
| `sow_versions` | Immutable versions    | `sow_id`, `version`, `content_tiptap` (JSONB), `structured` (JSONB `SowDocument`), `sha256`, `knowledge_version_id`, `llm_call_id`, `source` (`GENERATED`/`EDITED`), `approved_at`, `approved_by`, `is_locked`, `parent_version_id` | `GENERATED`, `IN_REVIEW`, `APPROVED`, `SUPERSEDED`, `GENERATION_FAILED` |
| `sow_impacts`  | Scope-change analysis | `from_version_id`, `to_version_id`, `changed_sections[]`, `affected_sub_skill_ids[]`                                                                                                                                                                      | —                                                                                |

## 7.6 ISATVON

| Table                | Purpose                             | Key fields                                                                                                                                                                                                                                                           | Status                 |
| -------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `macro_skills`     | Agents                              | `workflow_id`, `sow_version_id`, `macro_category` (`MSnn_<Name>`), `name`, `description`, `order`, `sow_refs[]`, `agent_ref`, `state`                                                                                                            | [§14.5](14-state-machines.md)                 |
| `sub_skills`       | Skills with ISATVON 1.4 definition  | `macro_skill_id`, `skill_id` (`MSnn.m_<Name>`), `skill_name`, `order`, `isatvon` (JSONB, structured [§10.3](10-isatvon-1-4-contract.md)), `isatvon_framework_version` (`1.4`), `reusable`, `sow_refs[]`, `validation_errors`, `state`, `content_sha256`, `deleted_at` | [§14.4](14-state-machines.md)                 |
| `workflow_schemas` | Workflow foundational`schema.sql` | `workflow_id`, `version`, `storage_key`, `sha256`, `source` (UPLOADED / GENERATED), `tables[]`, `status`                                                                                                                                               | `VALID`, `INVALID` |
| `sub_skill_edits`  | Field-level edit history            | `sub_skill_id`, `path`, `old`, `new`, `by`, `at`                                                                                                                                                                                                         | —                     |

## 7.7 Build and test evidence

| Table                   | Purpose                       | Key fields                                                                                                                                                                                                                                                                                                                    |
| ----------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sub_skill_manifests` | Manifest per Sub-Skill build  | `sub_skill_id`, `manifest` (JSONB), `sha256`, `schema_version`, `runtime_digest`, `is_frozen`                                                                                                                                                                                                                     |
| `code_attempts`       | Each generation/correction    | `sub_skill_id`, `manifest_id`, `attempt` (0..2), `template_version`, `bundle_key`, `files` (path → sha256), `llm_call_ids[]`, `correction_reason`, `status`                                                                                                                                                |
| `gate_results`        | One row per check             | `code_attempt_id`, `gate` (ISATVON_CONTRACT, LIBRARIES, AST_SYNTAX, AST_SKELETON, AST_CONFORMANCE, AST_DECOUPLING, AST_NO_MANUAL_LOOP, AST_HANDOFF, AST_IMPORTS, AST_SQL_SAFETY, RUFF, BLACK, MYPY, CLAMAV, BANDIT, IMPORT_RESOLUTION, SCHEMA_SQL), `status`, `findings` (JSONB), `tool_version`, `config_sha256` |
| `test_datasets`       | LLM-generated data            | `sub_skill_id`, `version`, `files_keys[]`, `sha256`, `synthetic=true`, `edited_by_fde`                                                                                                                                                                                                                            |
| `test_cases`          | ≥3 per Sub-Skill             | `dataset_id`, `name`, `kind` (PASS_FIRST, PASS_AFTER_RETRY, ESCALATION, NO_OP, EDGE, FDE), `input`, `initial_state`, `state_script`, `expected_terminal` (SUCCESS / ESCALATED / NO_OP), `expected_output`, `assertions`                                                                                     |
| `sandbox_runs`        | CubeSandbox executions        | `code_attempt_id`, `dataset_id`, `mode` (CUBESANDBOX, LOCAL_DOCKER_NONCERT), `vm_id`, `runtime_digest`, `duration_ms`, `exit_code`, `egress_log_key`, `failure_category`, `outputs_key`, `logs_key`                                                                                                     |
| `test_runs`           | Promptfoo results             | `sandbox_run_id`, `suite_sha256`, `total`, `passed`, `failed`, `results` (JSONB), `report_key`                                                                                                                                                                                                                  |
| `temporal_runs`       | Mirror of Temporal executions | `temporal_workflow_id`, `run_id`, `type`, `subject_type`, `subject_id`, `state`, `current_stage`                                                                                                                                                                                                                |

## 7.8 Certification, versions, artifacts, cross-cutting

| Table                  | Purpose                                    | Key fields                                                                                                                                                                  |
| ---------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `certifications`     | Sub-Skill, Macro and Workflow certificates | `level` (SUB_SKILL, MACRO, WORKFLOW), `subject_id`, `content_sha256`, `evidence` (JSONB index of ids + hashes), `reused_from_id`, `certified_at`, `is_frozen` |
| `workflow_versions`  | Versioned frozen Workflow                  | `workflow_id`, `version` (MAJOR.0.PATCH), `sow_version_id`, `workflow_manifest_id`, `change_reason`, `frozen_at`                                                |
| `workflow_manifests` | Workflow manifest                          | `workflow_id`, `manifest` (JSONB), `sha256`, `is_frozen`                                                                                                            |
| `artifacts`          | Index of every stored file                 | `tenant_id`, `project_id`, `kind`, `storage_key`, `sha256`, `size`, `content_type`, `is_frozen`                                                             |
| `llm_calls`          | LLM provenance                             | `alias`, `model`, `prompt_id`, `prompt_version`, `langfuse_trace_id`, `tokens_in/out`, `cost`, `latency_ms`, `context_manifest`, `outcome`              |
| `notifications`      | In-app notifications                       | `user_id`, `workflow_id`, `type`, `title`, `body`, `link`, `read_at`                                                                                          |
| `audit_log`          | Normal audit table                         | `tenant_id`, `actor_id`, `action`, `entity_type`, `entity_id`, `before`, `after`, `correlation_id`, `at`                                                  |

## 7.9 Relationships

```text
tenants ─< projects ─< workflows ─┬─< documents ─< document_versions >─< knowledge_sources >─ knowledge_versions
            │   ▲                 ├── sows ─< sow_versions ─< sow_impacts
            │   └ project_members ├─< macro_skills ─< sub_skills ─┬─< sub_skill_manifests
            └─< master_entries    │                               ├─< code_attempts ─< gate_results
                  ─< master_entry_versions                        │                └─< sandbox_runs ─< test_runs
                                  │                               ├─< test_datasets ─< test_cases
                                  │                               └─< certifications (SUB_SKILL)
                                  ├─< certifications (MACRO, WORKFLOW)
                                  └─< workflow_versions ── workflow_manifests
```

---
[← §6](06-repository-structure.md)   [Design index](README.md)   [§8 →](08-api-design.md)
