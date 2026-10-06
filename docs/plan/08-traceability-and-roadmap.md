> Docs: [Home](../README.md) · [Phases](../phases/README.md) · [Design](../design/README.md) · [Plan](../plan/README.md) · [Issue map](../ISSUE_MAP.md)

# PLAN — Traceability (AR-xx) and Final Roadmap

## PLAN §31. Traceability Rule and Matrix


### 31.1 Rule

Every task must be traceable to at least one of: a confirmed decision (C-xx, Section 3), an architecture requirement (AR-xx below), a design section in TD, a test scenario ([TD §19](../design/19-testing-strategy.md)) or an operational target (Section 28).

Recommended task format:

```text
TASK-SBAM-XXX
Requirement: AR-xx / C-xx (source: PD §n, ARCH-n, QA Xn)
Design: TD §n.n
Implementation: <module / file>
Tests: E0x, unit/API/Temporal test ids
Status: <Not Started | In Progress | Review | Done>
```

This keeps the plan, the technical design, the architecture documents and the tests synchronized.

### 31.2 Matrix

| AR    | Requirement (source)                                                                       | Component       | API ([TD §8](../design/08-api-design.md)) | Database ([TD §7](../design/07-database-design.md))                   | UI ([TD §9](../design/09-frontend-design.md))            | Temporal ([TD §13](../design/13-temporal-design.md))    | Test ([TD §19](../design/19-testing-strategy.md)) | Acceptance                                       |
| ----- | ------------------------------------------------------------------------------------------ | --------------- | ------------ | ----------------------------------- | ---------------------- | --------------------- | -------------- | ------------------------------------------------ |
| AR-01 | Two logins: Admin and FDE (QA A1, B1)                                                      | Identity        | 8.2          | `users`, `project_members`      | Login, portals         | —                    | E01            | Portal separation enforced                       |
| AR-02 | Multi-tenant isolation (QA B7)                                                             | Tenancy         | all          | `tenant_id` everywhere            | —                     | search attrs          | E02            | No cross-tenant access                           |
| AR-03 | Admin projects + 5 masters as Cognee context (QA C1–C4, ARCH-3)                           | Masters         | 8.3          | `projects`, `master_*`          | Admin portal           | knowledge WF          | E03            | Snapshot used in knowledge build                 |
| AR-04 | SBAM generates the Workflow from the approved SOW; FDE drives the project pipeline (owner correction) | Project pipeline + ISATVON | 8.4, 8.8 | `projects`, `workflows` | Project overview | `ProjectPipeline` | E04 | No API lets an FDE create a Workflow; Workflow exists only after SOW approval |
| AR-05 | Uppy upload, limits, ClamAV (QA D1–D4)                                                    | Ingestion       | 8.5          | `documents`                       | Upload                 | `DocumentIngestion` | E05, E06       | Unsafe files rejected                            |
| AR-06 | Basic OCR via LLM (QA D5)                                                                  | Ingestion       | 8.5          | `document_versions`               | Processing             | ingest                | E07            | Scanned pages extracted                          |
| AR-07 | Soft delete, 90-day recovery (QA D6)                                                       | Ingestion       | 8.5          | `deleted_at`                      | Recycle bin            | purge schedule        | E08            | Restore/purge works                              |
| AR-08 | Cognee graph per project, read-only explorer (QA E1, E3, E4)                               | Knowledge       | 8.6          | `knowledge_versions`              | Explorer               | knowledge             | E09            | Isolated, explorable                             |
| AR-09 | Information-preserving OKF Bundle (QA E2, PD §1b)                                         | Knowledge       | 8.6          | `artifacts`                       | Coverage               | knowledge             | E09            | 100% coverage                                    |
| AR-10 | One-shot cited SOW via Bifrost (QA F2, F3, PD §1c–d)                                     | SOW             | 8.7          | `sow_versions`, `llm_calls`     | SOW                    | SOW stage             | E10            | Cited valid SOW                                  |
| AR-11 | TipTap editing + export PDF/DOCX/MD (QA F2, F4)                                            | SOW             | 8.7          | `artifacts`                       | SOW                    | exports               | E11            | Exports valid                                    |
| AR-12 | FDE approval gate (QA B2, PD §2a)                                                         | SOW             | 8.7          | `sow_versions`                    | Approve                | signal                | E12            | Gate enforced                                    |
| AR-13 | Scope change → only affected rebuilt (QA F5)                                              | SOW + ISATVON   | 8.7          | `sub_skills.state`                | Impact preview         | rebuild signal        | E13            | Only affected rebuilt                            |
| AR-14 | Macro (agent, MSnn) → Sub-Skill (skill, MSnn.m), ISATVON 1.4 per Sub-Skill (QA G2, ISA14) | ISATVON         | 8.8          | `macro_skills`, `sub_skills`    | Workbench              | ISATVON stage         | E14            | All ISA14 elements + 5 loop bounds               |
| AR-15 | Grid safeguards (PD §2)                                                                   | Frontend        | 8.8          | —                                  | Workbench              | —                    | E15            | No crash, Generate gated                         |
| AR-16 | Excel export only (QA G3)                                                                  | ISATVON         | 8.8          | `artifacts`                       | Export                 | exports               | E14            | xlsx matches                                     |
| AR-17 | Skill Manifest in ISA14 structure as the codegen and SRIM contract (flow, QA H2, ISA14)    | Manifest        | 8.9          | `sub_skill_manifests`             | Viewer                 | build                 | E16            | ISA14 shape + deterministic hash                 |
| AR-18 | Hybrid Jinja2 + LLM codegen (QA I1, C-22)                                                  | Codegen         | 8.11         | `code_attempts`                   | Code viewer            | build                 | E16            | Skeleton intact, bodies from LLM                 |
| AR-19 | AST validates code vs manifest (flow, PD §3)                                              | Gates           | 8.10         | `gate_results`                    | Build dashboard        | build                 | E17            | Non-conforming code rejected                     |
| AR-20 | Correction: ISATVON + Python, 2 attempts (QA I4, I5)                                       | Correction      | 8.10         | `code_attempts`                   | Build dashboard        | build                 | E18            | Stops at 2 → FDE                                |
| AR-21 | Ruff, Black, MyPy strict, ClamAV (QA J2, J3, ARCH-3)                                       | Gates           | 8.10         | `gate_results`                    | Build dashboard        | build                 | E17            | All pass required                                |
| AR-22 | Runtime registry, Postgres only, all in Docker (QA J1)                                     | Registry        | 8.15         | `runtime_registry`                | Admin registry         | build                 | E16            | Missing deps fail                                |
| AR-23 | LLM test data, ≥3 Promptfoo tests (QA K2, K3, K5)                                         | Testing         | 8.12         | `test_cases`, `test_runs`       | Test panel             | test                  | E19            | ≥3 pass                                         |
| AR-24 | CubeSandbox on Linux/KVM, network allowed (QA K1, K4, PD §4a)                             | Sandbox         | 8.12         | `sandbox_runs`                    | Build dashboard        | test                  | E19            | Certifying runs isolated                         |
| AR-25 | Auto-certification Sub → Macro → Workflow (QA B4, H1)                                    | Certification   | 8.14         | `certifications`                  | Certification          | certify               | E20            | Only full evidence                               |
| AR-26 | Reuse unchanged certification (QA L3)                                                      | Certification   | 8.14         | `certifications`                  | Versions               | certify               | E13            | Unchanged reused                                 |
| AR-27 | MAJOR.0.PATCH, freeze, no revocation (QA L1, L2, PD §4d)                                  | Versioning      | 8.14         | triggers                            | Versions               | freeze                | E20            | Immutable                                        |
| AR-28 | Export package for SRIM; API future (QA M, H2)                                             | Export          | 8.14         | `artifacts`                       | Export                 | exports               | E20            | Checksums verify                                 |
| AR-29 | N = notify then next step; in-app alerts only (QA G4, Q2)                                  | Notifications   | 8.13         | `notifications`                   | Bell                   | notify activity       | E04–E20       | Every step notifies                              |
| AR-30 | Keycloak (QA B6)                                                                           | Identity        | 8.2          | —                                  | Login                  | —                    | E01            | OIDC working                                     |
| AR-31 | Next.js frontend (QA N1)                                                                   | Frontend        | —           | —                                  | all                    | —                    | E01–E20       | —                                               |
| AR-32 | Temporal self-hosted; Compose on VMs; 4 environments; GitHub Actions (QA N3–N8)           | Platform        | —           | —                                  | —                     | all                   | Deploy drill   | Repeatable deploys                               |
| AR-33 | Local disk storage behind S3 interface (QA N5)                                             | Storage         | —           | `artifacts`                       | —                     | —                    | Storage tests  | Swap to S3 = config                              |
| AR-34 | OpenRouter via Bifrost (QA O1)                                                             | LLM             | —           | `llm_calls`                       | —                     | all LLM               | Provider tests | All calls via Bifrost                            |
| AR-35 | OTEL + Langfuse + PostHog (QA Q1)                                                          | Observability   | —           | —                                  | —                     | interceptors          | Alert drill    | Correlated traces                                |
| AR-36 | Normal audit table (QA P2)                                                                 | Audit           | 8.16         | `audit_log`                       | Activity               | —                    | E02            | All actions audited                              |
| AR-37 | Frozen BAM 0.2 masters workbook, import/export (MSC)                                       | Masters         | 8.3          | `master_*`                        | Masters screen         | —                    | E03            | Round-trip identical; structure changes rejected |
| AR-38 | 43 frozen filetypes drive ingestion; private table (MSC)                                   | Ingestion       | 8.5          | `master_entries`                  | Upload                 | ingest                | E05            | Only enabled types accepted; FDE cannot edit     |
| AR-43 | Master documents routed by Filetype default route (MSC README §9)                         | Masters         | 8.3          | `master_documents`                | Master documents panel | ingest                | E03            | Routed to the right master                       |
| AR-44 | RAG Reference → Cognee node type/parent; Organization as graph root (MSC)                 | Knowledge       | 8.6          | `master_entry_versions`           | Explorer               | knowledge             | E09            | Master nodes linked to the Organization root     |
| AR-39 | Action tool + independent Fixed Check tool, no manual loops (ISA14 T, A)                   | Codegen + AST   | 8.11         | `code_attempts`, `gate_results` | Code viewer            | build                 | E17            | Decoupling and no-loop checks pass               |
| AR-40 | 5 mandatory loop bounds enforced in tests (ISA14 V)                                        | Loop harness    | 8.12         | `sandbox_runs`, `test_runs`     | Build & Test           | test                  | E19            | PASS_FIRST + ESCALATION pass                     |
| AR-41 | Append-only state handoff (ISA14 O)                                                        | Codegen + tests | 8.12         | `test_runs`                       | Harness trace          | test                  | E19            | Handoff rows appended, no updates                |
| AR-42 | Foundational PostgreSQL schema (ISA14)                                                     | Schema SQL      | 8.9          | `workflow_schemas`                | Schema view            | build                 | E16            | Applies cleanly                                  |

## PLAN §32. Final Implementation Roadmap


| Order | Phase             | Deliverables                                                    | Dependencies | Tasks    | Testing gate               | Exit criteria                       |
| ----- | ----------------- | --------------------------------------------------------------- | ------------ | -------- | -------------------------- | ----------------------------------- |
| 1st   | 0 Baseline        | Contracts v1, state machines, inputs as fixtures, spike reports | —           | 001–004 | Design review              | No blocking unknowns                |
| 2nd   | 1 Foundation      | Monorepo, Compose stack, Dockerfiles, CI, skeletons             | 0            | 005–013 | CI green,`make up`       | Stack runs on all dev machines      |
| 3rd   | 2 Identity        | Keycloak, tenancy, RBAC, audit                                  | 1            | 014–015 | RBAC + IDOR                | No cross-tenant access              |
| 4th   | 3 Admin portal    | Projects, masters, users                                        | 2            | 016–018 | Admin E2E                  | Masters snapshot ready (IP-1)       |
| 5th   | 4 Ingestion       | Workflow, Uppy, ClamAV, parsing, OCR, recycle bin               | 3            | 019–023 | Type fixtures, EICAR       | Files PARSED safely                 |
| 6th   | 5 Knowledge       | Cognee, OKF Bundle, explorer                                    | 4            | 024–027 | Isolation, coverage        | READY knowledge                     |
| 7th   | 6 SOW             | One-shot cited SOW, TipTap, export                              | 5            | 028–031 | Golden set                 | Editable cited SOW                  |
| 8th   | 7 Approval        | Gate, scope change, impact analysis                             | 6            | 032–033 | Gate tests                 | Approved SOW only (IP-2)            |
| 9th   | 8 ISATVON         | Macro → Sub-Skill breakdown, grid, Excel                       | 7            | 034–037 | Golden example, grid tests | Valid Sub-Skills                    |
| 10th  | 9 Manifest        | Sub-Skill + Workflow manifests, dependency check                | 8            | 038–039 | Determinism                | Stable hashes                       |
| 11th  | 10 Temporal       | Pipeline + Sub-Skill build workflows, notifications             | 9            | 040–041 | WF env + replay            | All paths tested                    |
| 12th  | 11 Codegen        | Skeleton templates, LLM bodies, correction, code viewer         | 10           | 042–045 | Snapshots, fixtures        | Tools assembled (IP-3)              |
| 13th  | 12 Gates          | AST conformance, static/security gates, runtime registry        | 11           | 046–049 | Negative fixtures          | Only valid code proceeds            |
| 14th  | 13 Test & sandbox | Test data, Promptfoo, CubeSandbox host + adapter, mocks         | 12           | 050–054 | Meta-test, egress test     | ≥3 cases pass in MicroVM           |
| 15th  | 14 Certification  | 3-level certification, versions, freeze, export                 | 13           | 055–058 | Tamper tests               | Frozen exportable Workflow (IP-4)   |
| 16th  | 15 E2E & security | E01–E20, security, performance, LLM quality                    | 2–14        | 059–061 | Green ×5                  | Targets met                         |
| 17th  | 16 Ops & go-live  | Environments, observability, backup, UAT                        | 15           | 062–065 | Restore + UAT              | First Workflow frozen in production |
| Later | Future            | Approval integrations, ingestion channels, SRIM handover, AWS   | MVP          | 066–069 | Per feature                | Per feature DoD                     |

**What to build first, second, third:**

1. Collect the three inputs and freeze the contracts.
2. Bring up the Docker stack, CI, Keycloak and tenancy.
3. Let the Admin set up projects and masters.
4. Follow the data through the system: ingestion → knowledge → SOW → approval → ISATVON → manifests. Meanwhile, Dev C builds the Temporal backbone, code generation and gates against fixture manifests.
5. Connect the real manifests to code generation, gates, sandbox and certification.
6. Freeze and export the Workflow.
7. Prove everything end to end, then operate it.
