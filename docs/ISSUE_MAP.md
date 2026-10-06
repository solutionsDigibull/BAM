# SBAM Issue Map

All work lives in [solutionsDigibull/BAM issues](https://github.com/solutionsDigibull/BAM/issues): **17 epics, 128 stories, 5 future items**. This page is generated from `issues_spec.py`. Issue bodies are the source of truth for scope and acceptance criteria.

Legend: **Stream** A platform/DevOps/security · B knowledge/LLM · C build pipeline · D frontend — **Size** S ≤1 d · M 2–3 d · L 4–5 d.

## Milestones

| Milestone | Goal / demo | Stories |
|---|---|---|
| M0 Foundation | Repo, Docker stack, CI, contracts, identity baseline, spikes | 36 |
| M1 Admin + Ingestion | Admin masters, project workspace, upload → ClamAV → parsed (IP-1) | 23 |
| M2 Knowledge + SOW | Cognee + OKF, one-shot SOW, FDE approval (IP-2) | 17 |
| M3 Breakdown + Build | ISATVON 1.4 breakdown, manifests, codegen, gates (IP-3) | 31 |
| M4 Test + Certify | Loop harness in CubeSandbox, Promptfoo, certification, freeze, export (IP-4) | 15 |
| M5 Harden + Go-live | E2E, security, performance, environments, UAT, go-live | 6 |
| Future | Post-MVP scope | 5 |

## Start here (no dependencies)

- [#18](https://github.com/solutionsDigibull/BAM/issues/18) `E0.1` ADRs for confirmed decisions and open-item defaults — A Platform, S
- [#19](https://github.com/solutionsDigibull/BAM/issues/19) `E0.2` ISATVON 1.4 Pydantic contract + gate-expression grammar — C Build, L
- [#21](https://github.com/solutionsDigibull/BAM/issues/21) `E0.4` Admin masters schemas (BAM 0.2) + RAG Reference + stable-ID/status rules — B Knowledge/LLM, M
- [#23](https://github.com/solutionsDigibull/BAM/issues/23) `E0.6` State-machine definitions as code (project pipeline, document, SOW, Workflow, macro, sub-skill) — A Platform, S
- [#24](https://github.com/solutionsDigibull/BAM/issues/24) `E0.7` API conventions + problem+json error schema — A Platform, S
- [#25](https://github.com/solutionsDigibull/BAM/issues/25) `E0.8` Golden fixture pack — B Knowledge/LLM, S
- [#26](https://github.com/solutionsDigibull/BAM/issues/26) `E0.9` Spike: Cognee default stores + OKF bundle — B Knowledge/LLM, M
- [#30](https://github.com/solutionsDigibull/BAM/issues/30) `E0.12` Spike: Promptfoo against a FastMCP tool — C Build, S
- [#31](https://github.com/solutionsDigibull/BAM/issues/31) `E0.13` Spike: Keycloak Organizations + Auth.js + FastAPI JWT — A Platform, S
- [#27](https://github.com/solutionsDigibull/BAM/issues/27) `E0.14` Prerequisites: GHCR, OpenRouter key, KVM host, dev VM, Netbird — A Platform, S
- [#32](https://github.com/solutionsDigibull/BAM/issues/32) `E1.1` Monorepo scaffold and tooling — A Platform, M

## Critical path (≈66 dev-days end to end)

These issues gate the final delivery. Keep them moving first:

[#32](https://github.com/solutionsDigibull/BAM/issues/32) `E1.1` → [#38](https://github.com/solutionsDigibull/BAM/issues/38) `E1.9` → [#39](https://github.com/solutionsDigibull/BAM/issues/39) `E1.10` → [#47](https://github.com/solutionsDigibull/BAM/issues/47) `E2.1` → [#48](https://github.com/solutionsDigibull/BAM/issues/48) `E2.2` → [#54](https://github.com/solutionsDigibull/BAM/issues/54) `E3.1` → [#55](https://github.com/solutionsDigibull/BAM/issues/55) `E3.2` → [#60](https://github.com/solutionsDigibull/BAM/issues/60) `E3.7` → [#70](https://github.com/solutionsDigibull/BAM/issues/70) `E4.4` → [#71](https://github.com/solutionsDigibull/BAM/issues/71) `E4.5` → [#80](https://github.com/solutionsDigibull/BAM/issues/80) `E5.2` → [#81](https://github.com/solutionsDigibull/BAM/issues/81) `E5.3` → [#86](https://github.com/solutionsDigibull/BAM/issues/86) `E6.2` → [#87](https://github.com/solutionsDigibull/BAM/issues/87) `E6.3` → [#90](https://github.com/solutionsDigibull/BAM/issues/90) `E7.1` → [#91](https://github.com/solutionsDigibull/BAM/issues/91) `E8.1` → [#92](https://github.com/solutionsDigibull/BAM/issues/92) `E8.2` → [#93](https://github.com/solutionsDigibull/BAM/issues/93) `E8.3` → [#101](https://github.com/solutionsDigibull/BAM/issues/101) `E9.1` → [#125](https://github.com/solutionsDigibull/BAM/issues/125) `E13.4` → [#127](https://github.com/solutionsDigibull/BAM/issues/127) `E13.6` → [#129](https://github.com/solutionsDigibull/BAM/issues/129) `E14.1` → [#130](https://github.com/solutionsDigibull/BAM/issues/130) `E14.2` → [#132](https://github.com/solutionsDigibull/BAM/issues/132) `E14.4` → [#133](https://github.com/solutionsDigibull/BAM/issues/133) `E14.5` → [#134](https://github.com/solutionsDigibull/BAM/issues/134) `E14.6` → [#136](https://github.com/solutionsDigibull/BAM/issues/136) `E15.2` → [#145](https://github.com/solutionsDigibull/BAM/issues/145) `E16.6`

## Epics and stories

### [#1](https://github.com/solutionsDigibull/BAM/issues/1) Phase 0 — Architecture, Contracts and Inputs Baseline

*M0 Foundation* — Freeze contracts and resolve unknowns so four developers can build in parallel without rework.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#18](https://github.com/solutionsDigibull/BAM/issues/18) | `E0.1` | ADRs for confirmed decisions and open-item defaults | A | S | P0 | M0 | — |
| [#19](https://github.com/solutionsDigibull/BAM/issues/19) | `E0.2` | ISATVON 1.4 Pydantic contract + gate-expression grammar | C | L | P0 | M0 | — |
| [#20](https://github.com/solutionsDigibull/BAM/issues/20) | `E0.3` | Skill Manifest + Workflow manifest models and RFC 8785 hashing | C | M | P0 | M0 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#21](https://github.com/solutionsDigibull/BAM/issues/21) | `E0.4` | Admin masters schemas (BAM 0.2) + RAG Reference + stable-ID/status rules | B | M | P0 | M0 | — |
| [#22](https://github.com/solutionsDigibull/BAM/issues/22) | `E0.5` | SowDocument model (ISATVON 1.4-derived sections) | B | S | P0 | M0 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#23](https://github.com/solutionsDigibull/BAM/issues/23) | `E0.6` | State-machine definitions as code (project pipeline, document, SOW, Workflow, macro, sub-skill) | A | S | P0 | M0 | — |
| [#24](https://github.com/solutionsDigibull/BAM/issues/24) | `E0.7` | API conventions + problem+json error schema | A | S | P0 | M0 | — |
| [#25](https://github.com/solutionsDigibull/BAM/issues/25) | `E0.8` | Golden fixture pack | B | S | P0 | M0 | — |
| [#26](https://github.com/solutionsDigibull/BAM/issues/26) | `E0.9` | Spike: Cognee default stores + OKF bundle | B | M | P0 | M0 | — |
| [#28](https://github.com/solutionsDigibull/BAM/issues/28) | `E0.10` | Spike: CubeSandbox on Linux/KVM host | A | M | P0 | M0 | [#27](https://github.com/solutionsDigibull/BAM/issues/27) |
| [#29](https://github.com/solutionsDigibull/BAM/issues/29) | `E0.11` | Spike: hybrid codegen (Jinja2 skeleton + LLM bodies) on MS01.1 | C | M | P0 | M0 | [#20](https://github.com/solutionsDigibull/BAM/issues/20) |
| [#30](https://github.com/solutionsDigibull/BAM/issues/30) | `E0.12` | Spike: Promptfoo against a FastMCP tool | C | S | P0 | M0 | — |
| [#31](https://github.com/solutionsDigibull/BAM/issues/31) | `E0.13` | Spike: Keycloak Organizations + Auth.js + FastAPI JWT | A | S | P0 | M0 | — |
| [#27](https://github.com/solutionsDigibull/BAM/issues/27) | `E0.14` | Prerequisites: GHCR, OpenRouter key, KVM host, dev VM, Netbird | A | S | P0 | M0 | — |

### [#2](https://github.com/solutionsDigibull/BAM/issues/2) Phase 1 — Repository Foundation and Containerized Dev Stack

*M0 Foundation* — Fresh monorepo, full Docker Compose stack and CI so every developer runs SBAM locally on day one.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#32](https://github.com/solutionsDigibull/BAM/issues/32) | `E1.1` | Monorepo scaffold and tooling | A | M | P0 | M0 | — |
| [#33](https://github.com/solutionsDigibull/BAM/issues/33) | `E1.2` | Contracts package: Pydantic → JSON Schema → TypeScript generation + drift check | C | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32), [#19](https://github.com/solutionsDigibull/BAM/issues/19), [#20](https://github.com/solutionsDigibull/BAM/issues/20), [#21](https://github.com/solutionsDigibull/BAM/issues/21) |
| [#34](https://github.com/solutionsDigibull/BAM/issues/34) | `E1.3` | FastAPI core: app factory, settings, errors, correlation IDs, logging, health | A | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32), [#24](https://github.com/solutionsDigibull/BAM/issues/24) |
| [#35](https://github.com/solutionsDigibull/BAM/issues/35) | `E1.4` | OpenTelemetry instrumentation baseline | A | S | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#36](https://github.com/solutionsDigibull/BAM/issues/36) | `E1.5` | Database: SQLAlchemy 2 + Alembic baseline + tenant-scoped repository base | A | M | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#37](https://github.com/solutionsDigibull/BAM/issues/37) | `E1.6` | StorageBackend interface + LocalDiskBackend + S3 stub | A | S | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34) |
| [#40](https://github.com/solutionsDigibull/BAM/issues/40) | `E1.7` | Temporal client, worker bootstrap (--queues), run mirror | C | M | P0 | M0 | [#34](https://github.com/solutionsDigibull/BAM/issues/34), [#39](https://github.com/solutionsDigibull/BAM/issues/39) |
| [#41](https://github.com/solutionsDigibull/BAM/issues/41) | `E1.8` | State-machine service + audit writer | C | M | P0 | M0 | [#36](https://github.com/solutionsDigibull/BAM/issues/36), [#23](https://github.com/solutionsDigibull/BAM/issues/23) |
| [#38](https://github.com/solutionsDigibull/BAM/issues/38) | `E1.9` | Dockerfiles: web, api, worker, toolchain, promptfoo, skill-runtime base | A | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32) |
| [#39](https://github.com/solutionsDigibull/BAM/issues/39) | `E1.10` | Docker Compose core profile + init jobs + Makefile + README | A | M | P0 | M0 | [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#42](https://github.com/solutionsDigibull/BAM/issues/42) | `E1.11` | Compose profiles obs, analytics, sandbox-local | A | S | P1 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39) |
| [#43](https://github.com/solutionsDigibull/BAM/issues/43) | `E1.12` | GitHub Actions CI pipeline | A | M | P0 | M0 | [#38](https://github.com/solutionsDigibull/BAM/issues/38), [#33](https://github.com/solutionsDigibull/BAM/issues/33) |
| [#44](https://github.com/solutionsDigibull/BAM/issues/44) | `E1.13` | Next.js + Refine app shell, (admin)/(fde) route groups, UI kit | D | M | P0 | M0 | [#32](https://github.com/solutionsDigibull/BAM/issues/32) |
| [#45](https://github.com/solutionsDigibull/BAM/issues/45) | `E1.14` | Refine data provider, problem+json mapping, API proxy route | D | S | P0 | M0 | [#44](https://github.com/solutionsDigibull/BAM/issues/44), [#33](https://github.com/solutionsDigibull/BAM/issues/33) |
| [#46](https://github.com/solutionsDigibull/BAM/issues/46) | `E1.15` | Bifrost container + mock LLM provider for dev/tests | B | S | P0 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39) |

### [#3](https://github.com/solutionsDigibull/BAM/issues/3) Phase 2 — Identity, Multi-Tenancy and RBAC

*M0 Foundation* — Keycloak, tenant isolation and the two portals before any client data enters.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#47](https://github.com/solutionsDigibull/BAM/issues/47) | `E2.1` | Keycloak realm, clients, roles, tenant mapper, realm export | A | M | P0 | M0 | [#39](https://github.com/solutionsDigibull/BAM/issues/39), [#31](https://github.com/solutionsDigibull/BAM/issues/31) |
| [#48](https://github.com/solutionsDigibull/BAM/issues/48) | `E2.2` | FastAPI JWT validation + authorization chain | A | M | P0 | M0 | [#47](https://github.com/solutionsDigibull/BAM/issues/47), [#36](https://github.com/solutionsDigibull/BAM/issues/36) |
| [#49](https://github.com/solutionsDigibull/BAM/issues/49) | `E2.3` | Tenants + users mirror + super-admin tenant APIs | A | S | P0 | M0 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |
| [#50](https://github.com/solutionsDigibull/BAM/issues/50) | `E2.4` | RBAC APIs: invite users, roles, project assignment | A | S | P0 | M0 | [#49](https://github.com/solutionsDigibull/BAM/issues/49) |
| [#51](https://github.com/solutionsDigibull/BAM/issues/51) | `E2.5` | Admin and FDE login entry points (Auth.js Keycloak) | D | S | P0 | M0 | [#44](https://github.com/solutionsDigibull/BAM/issues/44), [#47](https://github.com/solutionsDigibull/BAM/issues/47) |
| [#52](https://github.com/solutionsDigibull/BAM/issues/52) | `E2.6` | Users & Roles screen (Admin) | D | S | P0 | M0 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#50](https://github.com/solutionsDigibull/BAM/issues/50) |
| [#53](https://github.com/solutionsDigibull/BAM/issues/53) | `E2.7` | RBAC matrix + IDOR test suite | A | S | P0 | M0 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |

### [#4](https://github.com/solutionsDigibull/BAM/issues/4) Phase 3 — Admin Portal: Projects and Masters (BAM 0.2)

*M1 Admin + Ingestion* — Admins create projects and maintain the frozen BAM 0.2 masters that give Cognee reference context.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#54](https://github.com/solutionsDigibull/BAM/issues/54) | `E3.1` | Projects API (create, edit, archive, assign FDEs, context notes) | A | S | P0 | M1 | [#48](https://github.com/solutionsDigibull/BAM/issues/48) |
| [#55](https://github.com/solutionsDigibull/BAM/issues/55) | `E3.2` | Masters engine: types, entries, versions, scope, snapshot | A | M | P0 | M1 | [#54](https://github.com/solutionsDigibull/BAM/issues/54), [#21](https://github.com/solutionsDigibull/BAM/issues/21) |
| [#56](https://github.com/solutionsDigibull/BAM/issues/56) | `E3.3` | Workbook import with frozen-structure validation | A | M | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#57](https://github.com/solutionsDigibull/BAM/issues/57) | `E3.4` | Workbook export + empty template | A | S | P0 | M1 | [#56](https://github.com/solutionsDigibull/BAM/issues/56) |
| [#58](https://github.com/solutionsDigibull/BAM/issues/58) | `E3.5` | Stable-ID and status extraction + status write-back into Misc | A | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#59](https://github.com/solutionsDigibull/BAM/issues/59) | `E3.6` | RAG Reference → Cognee node mapping | B | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#60](https://github.com/solutionsDigibull/BAM/issues/60) | `E3.7` | Filetype table (43, frozen, read-only) + loader/route configuration | A | S | P0 | M1 | [#55](https://github.com/solutionsDigibull/BAM/issues/55) |
| [#63](https://github.com/solutionsDigibull/BAM/issues/63) | `E3.8` | Master documents: upload, route by filetype, admin override | A | M | P1 | M1 | [#60](https://github.com/solutionsDigibull/BAM/issues/60), [#62](https://github.com/solutionsDigibull/BAM/issues/62) |
| [#64](https://github.com/solutionsDigibull/BAM/issues/64) | `E3.9` | Admin Projects screens | D | S | P0 | M1 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#54](https://github.com/solutionsDigibull/BAM/issues/54) |
| [#65](https://github.com/solutionsDigibull/BAM/issues/65) | `E3.10` | Masters screens (tabs, tooltips, chips, RAG editor, import/export wizard) | D | L | P0 | M1 | [#56](https://github.com/solutionsDigibull/BAM/issues/56), [#58](https://github.com/solutionsDigibull/BAM/issues/58), [#64](https://github.com/solutionsDigibull/BAM/issues/64) |
| [#66](https://github.com/solutionsDigibull/BAM/issues/66) | `E3.11` | Master documents panel | D | S | P1 | M1 | [#63](https://github.com/solutionsDigibull/BAM/issues/63), [#65](https://github.com/solutionsDigibull/BAM/issues/65) |
| [#68](https://github.com/solutionsDigibull/BAM/issues/68) | `E3.12` | Runtime registry view | D | S | P1 | M3 | [#67](https://github.com/solutionsDigibull/BAM/issues/67) |

### [#5](https://github.com/solutionsDigibull/BAM/issues/5) Phase 4 — Project Workspace and Document Ingestion

*M1 Admin + Ingestion* — FDEs open their assigned project, upload safely and start the pipeline; SBAM later creates the Workflow from the approved SOW.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#69](https://github.com/solutionsDigibull/BAM/issues/69) | `E4.1` | Project workspace API + ProjectPipeline start + dashboard | C | M | P0 | M1 | [#54](https://github.com/solutionsDigibull/BAM/issues/54), [#40](https://github.com/solutionsDigibull/BAM/issues/40), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#61](https://github.com/solutionsDigibull/BAM/issues/61) | `E4.2` | tusd service + hooks + upload token + limits | B | M | P0 | M1 | [#37](https://github.com/solutionsDigibull/BAM/issues/37), [#54](https://github.com/solutionsDigibull/BAM/issues/54) |
| [#62](https://github.com/solutionsDigibull/BAM/issues/62) | `E4.3` | ClamAV service + scan activity + quarantine | B | S | P0 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61), [#40](https://github.com/solutionsDigibull/BAM/issues/40) |
| [#70](https://github.com/solutionsDigibull/BAM/issues/70) | `E4.4` | Type detection (magic bytes) vs Filetype config + SHA-256 dedupe | B | S | P0 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61), [#60](https://github.com/solutionsDigibull/BAM/issues/60) |
| [#71](https://github.com/solutionsDigibull/BAM/issues/71) | `E4.5` | Loaders: PDF (text), DOCX, XLSX, CSV, MD, TXT | B | M | P0 | M1 | [#62](https://github.com/solutionsDigibull/BAM/issues/62), [#70](https://github.com/solutionsDigibull/BAM/issues/70) |
| [#72](https://github.com/solutionsDigibull/BAM/issues/72) | `E4.6` | Loaders: EML/MSG (+attachments), JSON/XML/YAML/OpenAPI/Swagger/FastMCP spec, source code | B | M | P0 | M1 | [#71](https://github.com/solutionsDigibull/BAM/issues/71) |
| [#73](https://github.com/solutionsDigibull/BAM/issues/73) | `E4.7` | Archives (.zip, .tar.gz) decompressed into member documents | B | S | P1 | M1 | [#72](https://github.com/solutionsDigibull/BAM/issues/72) |
| [#74](https://github.com/solutionsDigibull/BAM/issues/74) | `E4.8` | Basic OCR via LLM vision for scanned PDF and images | B | M | P0 | M1 | [#71](https://github.com/solutionsDigibull/BAM/issues/71), [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#75](https://github.com/solutionsDigibull/BAM/issues/75) | `E4.9` | Soft delete, restore, 90-day purge schedule | B | S | P1 | M1 | [#61](https://github.com/solutionsDigibull/BAM/issues/61) |
| [#76](https://github.com/solutionsDigibull/BAM/issues/76) | `E4.10` | FDE project workspace + Upload screen (Uppy) | D | M | P0 | M1 | [#51](https://github.com/solutionsDigibull/BAM/issues/51), [#69](https://github.com/solutionsDigibull/BAM/issues/69), [#61](https://github.com/solutionsDigibull/BAM/issues/61) |
| [#77](https://github.com/solutionsDigibull/BAM/issues/77) | `E4.11` | Processing status + recycle bin screen | D | S | P0 | M1 | [#76](https://github.com/solutionsDigibull/BAM/issues/76), [#75](https://github.com/solutionsDigibull/BAM/issues/75) |

### [#6](https://github.com/solutionsDigibull/BAM/issues/6) Phase 5 — Knowledge Construction (Cognee, OKF, Explorer)

*M2 Knowledge + SOW* — Per-project knowledge graph from uploads + Admin context and an information-preserving OKF bundle.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#78](https://github.com/solutionsDigibull/BAM/issues/78) | `E5.1` | Cognee adapter: default stores, per-project dataset, Bifrost routing | B | M | P0 | M2 | [#26](https://github.com/solutionsDigibull/BAM/issues/26), [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#80](https://github.com/solutionsDigibull/BAM/issues/80) | `E5.2` | Knowledge Temporal stage: add uploads + master snapshot, cognify | B | M | P0 | M2 | [#78](https://github.com/solutionsDigibull/BAM/issues/78), [#71](https://github.com/solutionsDigibull/BAM/issues/71), [#59](https://github.com/solutionsDigibull/BAM/issues/59), [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#81](https://github.com/solutionsDigibull/BAM/issues/81) | `E5.3` | OKF bundle build + validation + coverage | B | M | P0 | M2 | [#80](https://github.com/solutionsDigibull/BAM/issues/80) |
| [#82](https://github.com/solutionsDigibull/BAM/issues/82) | `E5.4` | Graph read API (paging, search, node detail with sources) | B | S | P0 | M2 | [#80](https://github.com/solutionsDigibull/BAM/issues/80) |
| [#83](https://github.com/solutionsDigibull/BAM/issues/83) | `E5.5` | Knowledge Explorer (Xyflow, read-only) | D | M | P1 | M2 | [#82](https://github.com/solutionsDigibull/BAM/issues/82) |
| [#84](https://github.com/solutionsDigibull/BAM/issues/84) | `E5.6` | Knowledge status and coverage panel | D | S | P0 | M2 | [#81](https://github.com/solutionsDigibull/BAM/issues/81) |

### [#7](https://github.com/solutionsDigibull/BAM/issues/7) Phase 6 — SOW Generation, Editing and Export

*M2 Knowledge + SOW* — One-shot cited SOW from the OKF bundle; FDE reviews, edits, versions and exports it.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#85](https://github.com/solutionsDigibull/BAM/issues/85) | `E6.1` | LLM layer: aliases, Pydantic AI wrapper, prompt registry, llm_calls, Langfuse | B | M | P0 | M2 | [#46](https://github.com/solutionsDigibull/BAM/issues/46) |
| [#86](https://github.com/solutionsDigibull/BAM/issues/86) | `E6.2` | One-shot SOW generation stage (context assembly, re-ask, citations) | B | L | P0 | M2 | [#85](https://github.com/solutionsDigibull/BAM/issues/85), [#81](https://github.com/solutionsDigibull/BAM/issues/81), [#22](https://github.com/solutionsDigibull/BAM/issues/22) |
| [#87](https://github.com/solutionsDigibull/BAM/issues/87) | `E6.3` | SOW versions, diff and regenerate APIs | B | S | P0 | M2 | [#86](https://github.com/solutionsDigibull/BAM/issues/86) |
| [#88](https://github.com/solutionsDigibull/BAM/issues/88) | `E6.4` | SOW export: PDF, DOCX, Markdown | B | S | P1 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87) |
| [#89](https://github.com/solutionsDigibull/BAM/issues/89) | `E6.5` | SOW screen: TipTap editor, citations panel, versions/diff | D | L | P0 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |

### [#8](https://github.com/solutionsDigibull/BAM/issues/8) Phase 7 — SOW Approval (HITL) and Scope-Change Versioning

*M2 Knowledge + SOW* — FDE approval is the hard gate; approval makes SBAM create the Workflow; scope change rebuilds only what changed.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#90](https://github.com/solutionsDigibull/BAM/issues/90) | `E7.1` | Hash-bound SOW approval + lock + pipeline signal | B | S | P0 | M2 | [#87](https://github.com/solutionsDigibull/BAM/issues/87), [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#95](https://github.com/solutionsDigibull/BAM/issues/95) | `E7.2` | New SOW version + impact analysis + selective invalidation | B | M | P1 | M3 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#94](https://github.com/solutionsDigibull/BAM/issues/94) |
| [#96](https://github.com/solutionsDigibull/BAM/issues/96) | `E7.3` | Approve UI + impact preview | D | S | P0 | M2 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#89](https://github.com/solutionsDigibull/BAM/issues/89) |

### [#9](https://github.com/solutionsDigibull/BAM/issues/9) Phase 8 — ISATVON 1.4 Breakdown and Workbench

*M3 Breakdown + Build* — SBAM creates the Workflow and breaks the approved SOW into MSnn Macro Skills and MSnn.m Sub-Skills with full ISATVON 1.4; FDE reviews in AG Grid.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#91](https://github.com/solutionsDigibull/BAM/issues/91) | `E8.1` | SBAM creates the Workflow on SOW approval | C | S | P0 | M3 | [#90](https://github.com/solutionsDigibull/BAM/issues/90), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#92](https://github.com/solutionsDigibull/BAM/issues/92) | `E8.2` | Macro Skill extraction (pass 1) | B | M | P0 | M3 | [#91](https://github.com/solutionsDigibull/BAM/issues/91), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#93](https://github.com/solutionsDigibull/BAM/issues/93) | `E8.3` | Sub-Skill ISATVON 1.4 extraction (pass 2) + validation | B | L | P0 | M3 | [#92](https://github.com/solutionsDigibull/BAM/issues/92), [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#94](https://github.com/solutionsDigibull/BAM/issues/94) | `E8.4` | ISATVON edit/delete/reorder/regenerate APIs + field audit | B | M | P0 | M3 | [#93](https://github.com/solutionsDigibull/BAM/issues/93) |
| [#97](https://github.com/solutionsDigibull/BAM/issues/97) | `E8.5` | ISATVON Excel export (openpyxl) | B | S | P1 | M3 | [#94](https://github.com/solutionsDigibull/BAM/issues/94) |
| [#98](https://github.com/solutionsDigibull/BAM/issues/98) | `E8.6` | Grid metadata schema endpoint | B | S | P0 | M3 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#99](https://github.com/solutionsDigibull/BAM/issues/99) | `E8.7` | AG Grid workbench (tree, ISATVON column groups, editors, safeguards) | D | L | P0 | M3 | [#98](https://github.com/solutionsDigibull/BAM/issues/98), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |
| [#100](https://github.com/solutionsDigibull/BAM/issues/100) | `E8.8` | Gate-expression editor, Sub-Skill drawer, I/O dependency view | D | M | P0 | M3 | [#99](https://github.com/solutionsDigibull/BAM/issues/99) |

### [#10](https://github.com/solutionsDigibull/BAM/issues/10) Phase 9 — Skill Manifest and Workflow schema.sql

*M3 Breakdown + Build* — Each Sub-Skill gets its ISA14-format Skill Manifest; the Workflow gets a validated foundational schema.sql.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#101](https://github.com/solutionsDigibull/BAM/issues/101) | `E9.1` | Skill Manifest builder (ISA14 shape + sbam_build_record) | C | M | P0 | M3 | [#20](https://github.com/solutionsDigibull/BAM/issues/20), [#93](https://github.com/solutionsDigibull/BAM/issues/93) |
| [#102](https://github.com/solutionsDigibull/BAM/issues/102) | `E9.2` | loop_spec compiler + safe expression evaluator | C | M | P0 | M3 | [#19](https://github.com/solutionsDigibull/BAM/issues/19) |
| [#103](https://github.com/solutionsDigibull/BAM/issues/103) | `E9.3` | Library check vs runtime registry | C | S | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#104](https://github.com/solutionsDigibull/BAM/issues/104) | `E9.4` | Workflow schema.sql builder + validation | B | M | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101) |
| [#105](https://github.com/solutionsDigibull/BAM/issues/105) | `E9.5` | Workflow manifest draft | C | S | P0 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#104](https://github.com/solutionsDigibull/BAM/issues/104) |
| [#106](https://github.com/solutionsDigibull/BAM/issues/106) | `E9.6` | Manifest + schema.sql viewer (Monaco) | D | S | P1 | M3 | [#101](https://github.com/solutionsDigibull/BAM/issues/101) |

### [#11](https://github.com/solutionsDigibull/BAM/issues/11) Phase 10 — Temporal Orchestration (ProjectPipeline + SubSkillBuild)

*M3 Breakdown + Build* — One durable backbone with signals, retries, correction loops and notifications that every gate plugs into.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#79](https://github.com/solutionsDigibull/BAM/issues/79) | `E10.1` | ProjectPipeline skeleton with stub activities, signals and waits | C | M | P0 | M1 | [#40](https://github.com/solutionsDigibull/BAM/issues/40), [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#107](https://github.com/solutionsDigibull/BAM/issues/107) | `E10.2` | SubSkillBuild workflow + correction loop (≤2) + retry signal | C | M | P0 | M2 | [#79](https://github.com/solutionsDigibull/BAM/issues/79) |
| [#108](https://github.com/solutionsDigibull/BAM/issues/108) | `E10.3` | Parallel build scheduler (limit, I/O ordering) | C | S | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#109](https://github.com/solutionsDigibull/BAM/issues/109) | `E10.4` | Notifications service + API (N = Next/Notification) | A | S | P0 | M3 | [#41](https://github.com/solutionsDigibull/BAM/issues/41) |
| [#110](https://github.com/solutionsDigibull/BAM/issues/110) | `E10.5` | Notification bell + inbox | D | S | P0 | M3 | [#109](https://github.com/solutionsDigibull/BAM/issues/109), [#44](https://github.com/solutionsDigibull/BAM/issues/44) |
| [#111](https://github.com/solutionsDigibull/BAM/issues/111) | `E10.6` | Temporal replay tests in CI | C | S | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107) |

### [#12](https://github.com/solutionsDigibull/BAM/issues/12) Phase 11 — Hybrid Code Generation and Correction

*M3 Breakdown + Build* — Action tool + independent Fixed Check tool + handoff writer from each Skill Manifest (Jinja2 skeleton + LLM bodies).

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#112](https://github.com/solutionsDigibull/BAM/issues/112) | `E11.1` | fastmcp-loopy skeleton templates + golden snapshots | C | L | P0 | M2 | [#29](https://github.com/solutionsDigibull/BAM/issues/29), [#20](https://github.com/solutionsDigibull/BAM/issues/20) |
| [#113](https://github.com/solutionsDigibull/BAM/issues/113) | `E11.2` | LLM body generation (action, check, state queries) | C | M | P0 | M3 | [#112](https://github.com/solutionsDigibull/BAM/issues/112), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#114](https://github.com/solutionsDigibull/BAM/issues/114) | `E11.3` | Assembly + Black + bundle storage | C | S | P0 | M3 | [#113](https://github.com/solutionsDigibull/BAM/issues/113) |
| [#115](https://github.com/solutionsDigibull/BAM/issues/115) | `E11.4` | Correction activity (ISATVON patch + Python edit, max 2) | C | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114), [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#116](https://github.com/solutionsDigibull/BAM/issues/116) | `E11.5` | Code viewer (attempts, diffs, findings markers) | D | S | P1 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114) |

### [#13](https://github.com/solutionsDigibull/BAM/issues/13) Phase 12 — AST and Static Validation Gates

*M3 Breakdown + Build* — Only syntactically valid, manifest-conformant, decoupled, loop-free, handoff-correct and statically clean code reaches the sandbox.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#67](https://github.com/solutionsDigibull/BAM/issues/67) | `E12.1` | skill-runtime image + runtime registry generation | A | M | P0 | M2 | [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#117](https://github.com/solutionsDigibull/BAM/issues/117) | `E12.2` | AST gate: syntax, skeleton integrity, manifest conformance | C | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114) |
| [#118](https://github.com/solutionsDigibull/BAM/issues/118) | `E12.3` | AST gate: decoupling, no manual loops, handoff, imports, SQL safety | C | M | P0 | M3 | [#117](https://github.com/solutionsDigibull/BAM/issues/117) |
| [#119](https://github.com/solutionsDigibull/BAM/issues/119) | `E12.4` | Static gate in toolchain image: Ruff, Black, MyPy strict, Bandit, ClamAV | C | M | P0 | M3 | [#114](https://github.com/solutionsDigibull/BAM/issues/114), [#38](https://github.com/solutionsDigibull/BAM/issues/38) |
| [#120](https://github.com/solutionsDigibull/BAM/issues/120) | `E12.5` | Import resolution in skill-runtime | C | S | P0 | M3 | [#67](https://github.com/solutionsDigibull/BAM/issues/67), [#114](https://github.com/solutionsDigibull/BAM/issues/114) |
| [#121](https://github.com/solutionsDigibull/BAM/issues/121) | `E12.6` | Build & Test dashboard | D | M | P0 | M3 | [#107](https://github.com/solutionsDigibull/BAM/issues/107), [#45](https://github.com/solutionsDigibull/BAM/issues/45) |

### [#14](https://github.com/solutionsDigibull/BAM/issues/14) Phase 13 — Test Data, Loop Harness, Promptfoo and CubeSandbox

*M4 Test + Certify* — Prove each Sub-Skill under the ISATVON 1.4 bounded loop in an isolated MicroVM with ≥3 passing scenarios.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#122](https://github.com/solutionsDigibull/BAM/issues/122) | `E13.1` | CubeSandbox host + template from skill-runtime + Netbird | A | L | P0 | M3 | [#28](https://github.com/solutionsDigibull/BAM/issues/28), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#123](https://github.com/solutionsDigibull/BAM/issues/123) | `E13.2` | Sandbox adapter (cubesandbox + local-docker non-certifying) | C | M | P0 | M4 | [#122](https://github.com/solutionsDigibull/BAM/issues/122), [#107](https://github.com/solutionsDigibull/BAM/issues/107) |
| [#124](https://github.com/solutionsDigibull/BAM/issues/124) | `E13.3` | Bounded-loop harness | C | L | P0 | M3 | [#102](https://github.com/solutionsDigibull/BAM/issues/102), [#67](https://github.com/solutionsDigibull/BAM/issues/67) |
| [#125](https://github.com/solutionsDigibull/BAM/issues/125) | `E13.4` | Testgen: loop scenarios, seed state, state scripts, files | B | M | P0 | M4 | [#101](https://github.com/solutionsDigibull/BAM/issues/101), [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#126](https://github.com/solutionsDigibull/BAM/issues/126) | `E13.5` | Mock API service for networked sources | B | S | P1 | M4 | [#125](https://github.com/solutionsDigibull/BAM/issues/125) |
| [#127](https://github.com/solutionsDigibull/BAM/issues/127) | `E13.6` | Promptfoo suite builder + runner + grid badges | C | M | P0 | M4 | [#124](https://github.com/solutionsDigibull/BAM/issues/124), [#125](https://github.com/solutionsDigibull/BAM/issues/125) |
| [#128](https://github.com/solutionsDigibull/BAM/issues/128) | `E13.7` | Test panel UI (cases + harness trace) | D | M | P0 | M4 | [#125](https://github.com/solutionsDigibull/BAM/issues/125), [#121](https://github.com/solutionsDigibull/BAM/issues/121) |

### [#15](https://github.com/solutionsDigibull/BAM/issues/15) Phase 14 — Certification, Versioning, Freeze and Export

*M4 Test + Certify* — Automatic Sub-Skill → Macro → Workflow certification, MAJOR.0.PATCH versions, immutable freeze and export package.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#129](https://github.com/solutionsDigibull/BAM/issues/129) | `E14.1` | Sub-Skill certification checklist + manifest APPROVED | C | M | P0 | M4 | [#127](https://github.com/solutionsDigibull/BAM/issues/127), [#123](https://github.com/solutionsDigibull/BAM/issues/123), [#119](https://github.com/solutionsDigibull/BAM/issues/119), [#118](https://github.com/solutionsDigibull/BAM/issues/118) |
| [#130](https://github.com/solutionsDigibull/BAM/issues/130) | `E14.2` | Macro + Workflow validation and certification | C | M | P0 | M4 | [#129](https://github.com/solutionsDigibull/BAM/issues/129), [#105](https://github.com/solutionsDigibull/BAM/issues/105) |
| [#131](https://github.com/solutionsDigibull/BAM/issues/131) | `E14.3` | Certification reuse for unchanged Sub-Skills | C | S | P0 | M4 | [#129](https://github.com/solutionsDigibull/BAM/issues/129) |
| [#132](https://github.com/solutionsDigibull/BAM/issues/132) | `E14.4` | Versioning MAJOR.0.PATCH + freeze + immutability triggers | C | M | P0 | M4 | [#130](https://github.com/solutionsDigibull/BAM/issues/130) |
| [#133](https://github.com/solutionsDigibull/BAM/issues/133) | `E14.5` | Export package (manifests, code, loop specs, schema.sql, evidence, checksums) | A | S | P0 | M4 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#134](https://github.com/solutionsDigibull/BAM/issues/134) | `E14.6` | Certification tree, versions/lineage and export UI | D | M | P0 | M4 | [#132](https://github.com/solutionsDigibull/BAM/issues/132), [#133](https://github.com/solutionsDigibull/BAM/issues/133) |

### [#16](https://github.com/solutionsDigibull/BAM/issues/16) Phase 15 — End-to-End Testing, Security and Performance

*M5 Harden + Go-live* — Prove the full journey works and fails safely in a production-like Docker environment.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#135](https://github.com/solutionsDigibull/BAM/issues/135) | `E15.1` | Playwright harness + scenarios E01–E10 | D | L | P0 | M2 | [#96](https://github.com/solutionsDigibull/BAM/issues/96), [#77](https://github.com/solutionsDigibull/BAM/issues/77) |
| [#136](https://github.com/solutionsDigibull/BAM/issues/136) | `E15.2` | Scenarios E11–E20 | D | L | P0 | M5 | [#134](https://github.com/solutionsDigibull/BAM/issues/134), [#135](https://github.com/solutionsDigibull/BAM/issues/135) |
| [#137](https://github.com/solutionsDigibull/BAM/issues/137) | `E15.3` | Security suite (IDOR, tokens, injection, tamper, egress, scans) | A | M | P0 | M5 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#138](https://github.com/solutionsDigibull/BAM/issues/138) | `E15.4` | Performance tests | B | M | P1 | M5 | [#132](https://github.com/solutionsDigibull/BAM/issues/132) |
| [#139](https://github.com/solutionsDigibull/BAM/issues/139) | `E15.5` | LLM golden sets + regression gate | B | M | P0 | M4 | [#93](https://github.com/solutionsDigibull/BAM/issues/93), [#113](https://github.com/solutionsDigibull/BAM/issues/113) |

### [#17](https://github.com/solutionsDigibull/BAM/issues/17) Phase 16 — Environments, Operations, UAT and Go-Live

*M5 Harden + Go-live* — Run SBAM reliably in dev, staging and production (Compose on VMs) with observability, backups and UAT sign-off.

| Issue | Key | Story | Stream | Size | Pri | Milestone | Blocked by |
|---|---|---|---|---|---|---|---|
| [#140](https://github.com/solutionsDigibull/BAM/issues/140) | `E16.1` | Grafana LGTM dashboards + operator alerts | A | M | P0 | M4 | [#42](https://github.com/solutionsDigibull/BAM/issues/42) |
| [#141](https://github.com/solutionsDigibull/BAM/issues/141) | `E16.2` | Langfuse projects + PostHog funnel/eval events | A | S | P0 | M4 | [#85](https://github.com/solutionsDigibull/BAM/issues/85) |
| [#142](https://github.com/solutionsDigibull/BAM/issues/142) | `E16.3` | dev/staging/prod Compose + reverse proxy + Netbird policies | A | M | P0 | M4 | [#43](https://github.com/solutionsDigibull/BAM/issues/43) |
| [#143](https://github.com/solutionsDigibull/BAM/issues/143) | `E16.4` | Deploy workflows + rollback | A | S | P0 | M5 | [#142](https://github.com/solutionsDigibull/BAM/issues/142) |
| [#144](https://github.com/solutionsDigibull/BAM/issues/144) | `E16.5` | Backups + restore drill | A | S | P1 | M5 | [#142](https://github.com/solutionsDigibull/BAM/issues/142) |
| [#145](https://github.com/solutionsDigibull/BAM/issues/145) | `E16.6` | Runbooks, UAT scripts and readiness review | A | M | P0 | M5 | [#136](https://github.com/solutionsDigibull/BAM/issues/136), [#143](https://github.com/solutionsDigibull/BAM/issues/143) |

### Future (post-MVP)

- [#146](https://github.com/solutionsDigibull/BAM/issues/146) Email / external approval integration for SOW
- [#147](https://github.com/solutionsDigibull/BAM/issues/147) API / webhook / cron / email / FastMCP-watchdog ingestion channels
- [#148](https://github.com/solutionsDigibull/BAM/issues/148) SRIM handover API, acknowledgement and environment switch
- [#149](https://github.com/solutionsDigibull/BAM/issues/149) AWS hosting, S3 backend, Object Lock and manifest signing
- [#150](https://github.com/solutionsDigibull/BAM/issues/150) Intent classifier for master documents (Jev / Laya evaluation)
